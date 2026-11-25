"""
Sign-in with Google or Apple from the iOS app (see app_sign_in.py):

  POST /auth/app/finish -- the app's web view posts the code the sign-in
                           sheet ended on, with its verifier; starts the
                           session there and sends it on to the site

Outside /api/, like the providers' own pages (routes/social.py): the web
view opens it itself, as a page, and is sent on to the site (or to its
sign-in page, with the reason, when the code is no good).
"""
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Request

import app_sign_in
import auth
import database as db
from api import SESSION_COOKIE, _set_session_cookie
from routes.social import _back_to_the_site

logger = logging.getLogger("uvicorn.error")

router = APIRouter()


@router.post("/auth/app/finish", include_in_schema=False)
async def finish_in_the_app(request: Request):
    # Three short fields, read by hand like Apple's form.
    body = (await request.body())[:2_000].decode("utf-8", "replace")
    form = {key: values[0] for key, values in parse_qs(body).items()}
    found = app_sign_in.use(form.get("code"), form.get("verifier"))
    if found is None:
        logger.warning("Sign-in from the app failed: the code is unknown, expired, used, or the verifier is wrong")
        return _back_to_the_site(form.get("lang"), "social_failed")
    user = db.get_user(found["user_id"])
    if user is None:  # the account was deleted in between
        return _back_to_the_site(found["language"], "social_failed")
    if user["banned_at"]:
        return _back_to_the_site(found["language"], "account_banned")

    auth.end_session(request.cookies.get(SESSION_COOKIE))  # a previous one in the app
    response = _back_to_the_site(found["language"])
    _set_session_cookie(response, auth.start_session(user["id"]), auth.SESSION_TTL)
    logger.info("Signed in from the app: user %s", user["id"])
    return response
