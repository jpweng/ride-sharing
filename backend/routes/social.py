"""
Sign-in with Google or Apple (see social.py):

  GET /api/auth/providers          -- the providers the sign-in page offers
  GET /auth/{provider}/start       -- sends the browser to the provider
  GET, POST /auth/{provider}/callback -- the provider sends it back (Google
                                      in the address, Apple in a posted form)

Started by the iOS app (?app=ios&app_challenge=...), a sign-in ends on the
app's own kind of address with a code, not on the site with a session: see
app_sign_in.py and routes/app_sign_in.py.

The two /auth/ pages are outside /api/: they are opened by the browser
itself, not called by the page's scripts, and Apple's form arrives from
its own site, which the check on /api/ would refuse (main.py). What
protects them is the `state` kept in a cookie by start and checked by the
callback.
"""
import logging
import os
from urllib.parse import parse_qs, urlencode

import anyio.to_thread
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import app_sign_in
import auth
import social
from api import COOKIE_SECURE, SESSION_COOKIE, _set_session_cookie, api_error

logger = logging.getLogger("uvicorn.error")

router = APIRouter()

# Remembers a sign-in started in this browser until the provider sends it
# back. SameSite=None: Apple's answer is a form posted from its site, and
# a Lax cookie wouldn't come with it. Without Secure (plain HTTP in
# testing) browsers refuse SameSite=None, so it is Lax there, and Apple
# can't be tested that way.
STATE_COOKIE = "__Host-oauth" if COOKIE_SECURE else "oauth"
STATE_SAMESITE = "none" if COOKIE_SECURE else "lax"

# A provider's own words for "the person said no".
CANCELLED = {"access_denied", "user_cancelled_authorize"}


def _base_url() -> str:
    # Never taken from the request's Host header (see routes/auth.py).
    return (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")


def _enabled_or_404(provider: str) -> None:
    if not social.is_enabled(provider):
        raise api_error(404, "provider_not_found", "No such sign-in provider")


@router.get("/api/auth/providers")
def list_providers():
    return {"providers": social.enabled()}


@router.get("/auth/{provider}/start", include_in_schema=False)
def start_sign_in(provider: str, lang: str = "en", app: str | None = None, app_challenge: str | None = None):
    _enabled_or_404(provider)
    if app is not None and (app not in app_sign_in.APPS or not app_sign_in.is_challenge(app_challenge)):
        # An app's sign-in sheet, without what its sign-in needs: back to the app, which says it failed.
        logger.warning("Sign-in from an app refused at the start: app %r, challenge given: %s",
                       app[:20], bool(app_challenge))
        return _back_to_the_app(lang, error="social_failed")
    url, remembered = social.start(provider, lang, _base_url(), app_challenge if app else None)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        STATE_COOKIE, remembered, max_age=social.STATE_TTL_S,
        path="/", secure=COOKIE_SECURE, httponly=True, samesite=STATE_SAMESITE,
    )
    return response


def _back_to_the_site(language: str | None = None, error: str | None = None) -> RedirectResponse:
    """To the app (303: a GET, also after Apple's POST), with the reason on
    the sign-in page when it failed (app.js shows errors.<code>)."""
    params = {}
    if language in auth.LANGUAGES:
        params["lang"] = language
    if error:
        params["signin_error"] = error
    response = RedirectResponse(f"/{'?' + urlencode(params) if params else ''}#/ride", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/", secure=COOKIE_SECURE, httponly=True, samesite=STATE_SAMESITE)
    return response


def _back_to_the_app(language: str | None, code: str | None = None, error: str | None = None) -> RedirectResponse:
    """To the iOS app, from the sign-in sheet it opened: its own kind of
    address closes the sheet and hands the app either a code to start its
    session with (routes/app_sign_in.py) or the reason it failed."""
    params = {"code": code} if code else {"error": error or "social_failed"}
    if language in auth.LANGUAGES:
        params["lang"] = language
    response = RedirectResponse(f"{app_sign_in.CALLBACK}?{urlencode(params)}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/", secure=COOKIE_SECURE, httponly=True, samesite=STATE_SAMESITE)
    return response


@router.api_route("/auth/{provider}/callback", methods=["GET", "POST"], include_in_schema=False)
async def finish_sign_in(provider: str, request: Request):
    _enabled_or_404(provider)
    if request.method == "POST":
        # Apple's form. Read by hand: it's a few short fields.
        body = (await request.body())[:20_000].decode("utf-8", "replace")
        answer = {key: values[0] for key, values in parse_qs(body).items()}
    else:
        answer = dict(request.query_params)

    remembered = social._unpack(request.cookies.get(STATE_COOKIE)) or {}
    language = remembered.get("language")
    # Started by the iOS app: the answer, good or bad, goes to the app.
    challenge = remembered.get("app_challenge")
    in_app = app_sign_in.is_challenge(challenge if isinstance(challenge, str) else None)

    def failed(code: str) -> RedirectResponse:
        return _back_to_the_app(language, error=code) if in_app else _back_to_the_site(language, code)

    if answer.get("error"):
        code = "social_cancelled" if answer["error"] in CANCELLED else "social_failed"
        if code == "social_failed":
            logger.warning("Sign-in with %s failed at the provider: %s", provider, answer["error"][:100])
        return failed(code)

    try:
        user, language = await anyio.to_thread.run_sync(
            lambda: social.identify(
                provider, answer.get("code"), answer.get("state"), request.cookies.get(STATE_COOKIE),
                _base_url(), apple_user=answer.get("user"),
            )
        )
    except social.SocialError as e:
        logger.warning("Sign-in with %s failed: %s", provider, e.reason)
        return failed(e.code)
    except auth.AuthError as e:
        return failed(e.code)

    if in_app:
        # No session here: this browser is the sign-in sheet, not the app.
        logger.info("Identified with %s for the app: user %s", provider, user["id"])
        return _back_to_the_app(language, code=app_sign_in.create(user["id"], challenge, language))

    auth.end_session(request.cookies.get(SESSION_COOKIE))  # a previous one in this browser
    response = _back_to_the_site(language)
    _set_session_cookie(response, auth.start_session(user["id"]), auth.SESSION_TTL)
    logger.info("Signed in with %s: user %s", provider, user["id"])
    return response
