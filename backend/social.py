"""
Sign-in with Google or Apple, next to the email link (auth.py). See
docs/social-login.md.

Each provider is switched on by its settings (environment variables) and
off without them: the sign-in page then doesn't offer it and its routes
answer 404, so nothing here can break the site before the keys exist.

  Google: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  Apple:  APPLE_CLIENT_ID (the Services ID), APPLE_TEAM_ID, APPLE_KEY_ID,
          APPLE_PRIVATE_KEY (the .p8 key: its text, or that base64-encoded
          on one line)

Both speak OpenID Connect; the flow is the authorization code flow, run by
the server (routes/social.py):

1. start(): the browser is sent to the provider with a random `state` and
   `nonce` (and, for Google, a PKCE challenge). They are kept in a
   short-lived cookie in that browser, so the answer can only be used by
   the browser that asked.
2. The provider sends the browser back with a `code` and the `state`
   (Google: in the address; Apple: in a form posted from its site, which is
   why that cookie is SameSite=None).
3. identify(): the state must match the cookie's; the code is exchanged for
   an ID token at the provider, with our client secret (for Apple, a
   short-lived token signed with our key); the ID token's signature (the
   provider's published keys), issuer, audience, expiry and nonce are
   checked. Its `sub` is the provider's identifier for the person, its
   `email` their address, which must be verified by the provider.
4. The account: the one already linked to that (provider, sub); else the
   one with that email address, which is then linked (the provider vouches
   that the person controls the address, as our own sign-in link would);
   else a new one. Then a session starts as with the email link, and a
   first sign-in still goes through the profile page (name, 18+, code of
   conduct), with the name the provider gave as a suggestion.

From the iOS app, the session starts in the app instead of the browser
that went to the provider: see app_sign_in.py.

Nothing is kept of the provider's tokens: they are only used to learn who
signed in.
"""
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlencode

import jwt
import requests

import auth
import database as db

logger = logging.getLogger("uvicorn.error")

REQUEST_TIMEOUT_S = 8
STATE_TTL_S = 600  # from leaving for the provider to coming back
JWKS_TTL_S = 3600
APPLE_CLIENT_SECRET_TTL_S = 300
ID_TOKEN_LEEWAY_S = 60  # clocks differ a little

PROVIDERS = {
    "google": {
        "issuers": ("https://accounts.google.com", "accounts.google.com"),
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
        "scope": "openid email profile",
        "pkce": True,
        "settings": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    },
    "apple": {
        "issuers": ("https://appleid.apple.com",),
        "authorize_url": "https://appleid.apple.com/auth/authorize",
        "token_url": "https://appleid.apple.com/auth/token",
        "jwks_url": "https://appleid.apple.com/auth/keys",
        "scope": "name email",
        # Apple posts the answer as a form when a name or email is asked for.
        "form_post": True,
        "pkce": False,
        "settings": ("APPLE_CLIENT_ID", "APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY"),
    },
}


class SocialError(Exception):
    """Sign-in with a provider failed; `code` is shown to the user as
    errors.<code>, `reason` goes to the log."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


def _setting(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_enabled(provider: str) -> bool:
    """Whether the provider's settings are all there."""
    config = PROVIDERS.get(provider)
    return bool(config) and all(_setting(name) for name in config["settings"])


def enabled() -> list[str]:
    """The providers the sign-in page offers, in a fixed order."""
    return [name for name in PROVIDERS if is_enabled(name)]


def _client_id(provider: str) -> str:
    return _setting("GOOGLE_CLIENT_ID" if provider == "google" else "APPLE_CLIENT_ID")


def redirect_uri(provider: str, base_url: str) -> str:
    """Where the provider sends the browser back; registered with the provider."""
    return f"{base_url.rstrip('/')}/auth/{provider}/callback"


# ---------------------------------------------------------------------
# Leaving for the provider
# ---------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pack(values: dict) -> str:
    return _b64url(json.dumps(values, separators=(",", ":")).encode())


def _unpack(cookie: str | None) -> dict | None:
    try:
        raw = (cookie or "").encode()
        values = json.loads(base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4)))
    except (ValueError, binascii.Error):
        return None
    return values if isinstance(values, dict) else None


def start(provider: str, language: str, base_url: str, app_challenge: str | None = None) -> tuple[str, str]:
    """(the provider's address to send the browser to, the value of the
    cookie that remembers this attempt in that browser). `app_challenge`:
    the attempt comes from the iOS app, which is handed a code at the end
    instead of a session (app_sign_in.py)."""
    config = PROVIDERS[provider]
    remembered = {
        "provider": provider,
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
        "language": language if language in auth.LANGUAGES else "en",
        "at": int(time.time()),
    }
    if app_challenge:
        remembered["app_challenge"] = app_challenge
    params = {
        "client_id": _client_id(provider),
        "redirect_uri": redirect_uri(provider, base_url),
        "response_type": "code",
        "scope": config["scope"],
        "state": remembered["state"],
        "nonce": remembered["nonce"],
    }
    if config.get("form_post"):
        params["response_mode"] = "form_post"
    if config["pkce"]:
        remembered["verifier"] = secrets.token_urlsafe(48)
        params["code_challenge"] = _b64url(hashlib.sha256(remembered["verifier"].encode()).digest())
        params["code_challenge_method"] = "S256"
    if provider == "google":
        params["prompt"] = "select_account"  # on a shared computer, not silently the last account
    return f"{config['authorize_url']}?{urlencode(params)}", _pack(remembered)


# ---------------------------------------------------------------------
# Coming back
# ---------------------------------------------------------------------
def _apple_private_key() -> str:
    key = _setting("APPLE_PRIVATE_KEY")
    if "BEGIN" in key:
        return key.replace("\\n", "\n")
    try:
        return base64.b64decode(key).decode()
    except (ValueError, binascii.Error) as e:
        raise SocialError("social_failed", f"APPLE_PRIVATE_KEY is neither a key nor base64 of one: {e}") from e


def _client_secret(provider: str) -> str:
    """Google: the secret itself. Apple: a short-lived token signed with our
    key, saying which team and which Services ID we are."""
    if provider == "google":
        return _setting("GOOGLE_CLIENT_SECRET")
    now = int(time.time())
    try:
        return jwt.encode(
            {
                "iss": _setting("APPLE_TEAM_ID"),
                "iat": now,
                "exp": now + APPLE_CLIENT_SECRET_TTL_S,
                "aud": PROVIDERS["apple"]["issuers"][0],
                "sub": _client_id("apple"),
            },
            _apple_private_key(),
            algorithm="ES256",
            headers={"kid": _setting("APPLE_KEY_ID")},
        )
    except (ValueError, TypeError, jwt.PyJWTError) as e:
        raise SocialError("social_failed", f"can't sign Apple's client secret: {e}") from e


def _exchange(provider: str, code: str, remembered: dict, base_url: str) -> str:
    """The code for the ID token, at the provider."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": _client_id(provider),
        "client_secret": _client_secret(provider),
        "redirect_uri": redirect_uri(provider, base_url),
    }
    if PROVIDERS[provider]["pkce"]:
        data["code_verifier"] = remembered.get("verifier", "")
    try:
        resp = requests.post(PROVIDERS[provider]["token_url"], data=data, timeout=REQUEST_TIMEOUT_S)
        answer = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise SocialError("social_failed", f"{provider}'s token service can't be reached: {e}") from e
    if resp.status_code != 200 or not isinstance(answer, dict) or not answer.get("id_token"):
        problem = answer.get("error") if isinstance(answer, dict) else None
        raise SocialError("social_failed", f"{provider} refused the code: {resp.status_code} {problem}")
    return answer["id_token"]


_jwks_cache: dict[str, tuple[float, list]] = {}
_jwks_lock = threading.Lock()


def _jwks(provider: str, refresh: bool = False) -> list[dict]:
    """The provider's published signing keys, kept for JWKS_TTL_S."""
    with _jwks_lock:
        cached = _jwks_cache.get(provider)
        if cached and not refresh and time.time() - cached[0] < JWKS_TTL_S:
            return cached[1]
    try:
        keys = requests.get(PROVIDERS[provider]["jwks_url"], timeout=REQUEST_TIMEOUT_S).json()["keys"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise SocialError("social_failed", f"{provider}'s signing keys can't be fetched: {e}") from e
    with _jwks_lock:
        _jwks_cache[provider] = (time.time(), keys)
    return keys


def _signing_key(provider: str, id_token: str):
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
    except jwt.PyJWTError as e:
        raise SocialError("social_failed", f"{provider}'s ID token is malformed: {e}") from e
    for refresh in (False, True):  # the provider may have rotated its keys since we fetched them
        for key in _jwks(provider, refresh=refresh):
            if key.get("kid") == kid:
                try:
                    return jwt.PyJWK(key).key
                except jwt.PyJWTError as e:
                    raise SocialError("social_failed", f"{provider}'s signing key is unusable: {e}") from e
    raise SocialError("social_failed", f"{provider}'s ID token is signed with an unknown key")


def _claims(provider: str, id_token: str, nonce: str) -> dict:
    """The ID token's content, once its signature, issuer, audience, expiry
    and nonce are checked."""
    key = _signing_key(provider, id_token)
    try:
        claims = jwt.decode(
            id_token, key, algorithms=["RS256"], audience=_client_id(provider), leeway=ID_TOKEN_LEEWAY_S,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise SocialError("social_failed", f"{provider}'s ID token doesn't check out: {e}") from e
    if claims["iss"] not in PROVIDERS[provider]["issuers"]:
        raise SocialError("social_failed", f"{provider}'s ID token comes from {claims['iss']!r}")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SocialError("social_failed", f"{provider}'s ID token isn't the answer to this sign-in")
    return claims


def _clean(value) -> str | None:
    value = " ".join(str(value or "").split())[:auth.MAX_NAME_LENGTH]
    return value or None


def _names(provider: str, claims: dict, apple_user: str | None) -> tuple[str | None, str | None]:
    """The person's first and last name as the provider gives them: Google
    in the ID token; Apple once, at the first sign-in, in a form field."""
    if provider == "google":
        return _clean(claims.get("given_name")), _clean(claims.get("family_name"))
    try:
        name = json.loads(apple_user or "{}").get("name") or {}
        return _clean(name.get("firstName")), _clean(name.get("lastName"))
    except (ValueError, AttributeError):
        return None, None


def identify(provider: str, code: str | None, state: str | None, cookie: str | None, base_url: str,
             apple_user: str | None = None) -> tuple[dict, str]:
    """The provider sent the browser back: (user, language). The caller
    starts the session (routes/social.py). Raises SocialError, or
    auth.AuthError for a banned account."""
    remembered = _unpack(cookie)
    if (
        not remembered or remembered.get("provider") != provider
        or not state or not hmac.compare_digest(str(remembered.get("state", "")), state)
        or time.time() - int(remembered.get("at") or 0) > STATE_TTL_S
    ):
        raise SocialError("social_failed", "the answer doesn't match a sign-in started in this browser")
    if not code:
        raise SocialError("social_failed", "no code in the provider's answer")

    claims = _claims(provider, _exchange(provider, code, remembered, base_url), remembered["nonce"])
    # Apple sends email_verified as "true"; Google as true.
    if not claims.get("email") or str(claims.get("email_verified")).lower() != "true":
        raise SocialError("social_email_unverified", f"{provider} gave no verified email address")
    try:
        email = auth.normalize_email(claims["email"])
    except auth.AuthError as e:
        raise SocialError("social_email_unverified", f"{provider} gave an unusable email address") from e

    language = remembered.get("language") if remembered.get("language") in auth.LANGUAGES else "en"
    user = _account(provider, str(claims["sub"]), email, language, _names(provider, claims, apple_user))
    if user["banned_at"]:
        raise auth.AuthError(403, "account_banned", "This account is closed")
    if user["is_demo"]:  # never: demo drivers have no email
        raise SocialError("social_failed", "a demo driver can't sign in")
    db.set_user_language(user["id"], language)
    return db.get_user(user["id"]), language


def _account(provider: str, subject: str, email: str, language: str, names: tuple) -> dict:
    """The account for this person at this provider: the one already
    linked; else the one with their (verified) email address, linked now;
    else a new one."""
    identity = _identity(provider, subject)
    if identity:
        user = db.get_user(identity["user_id"])
        if user:
            return user
    user = db.get_user_by_email(email)
    if user is None:
        try:
            # The name is chosen next (auth.complete_profile); until then nobody sees it.
            user = db.create_account(email, f"~{secrets.token_hex(8)}", language)
        except sqlite3.IntegrityError:  # created by a concurrent sign-in
            user = db.get_user_by_email(email)
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_identities (user_id, provider, subject, email, given_name, family_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider, subject) DO NOTHING
            """,
            (user["id"], provider, subject, email, names[0], names[1], db.now_iso()),
        )
    return user


def _identity(provider: str, subject: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_identities WHERE provider = ? AND subject = ?", (provider, subject)
        ).fetchone()
        return dict(row) if row else None


def suggested_name(user_id: int) -> dict | None:
    """The name a provider gave for this user, to fill in the profile page
    at their first sign-in: {first_name, last_name}, or None."""
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT given_name, family_name FROM user_identities
            WHERE user_id = ? AND (given_name IS NOT NULL OR family_name IS NOT NULL) ORDER BY id LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return {"first_name": row["given_name"], "last_name": row["family_name"]} if row else None
