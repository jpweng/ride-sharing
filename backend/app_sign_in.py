"""
Sign-in with Google or Apple from the iOS app (mobile/ios/). See
docs/app-sign-in.md.

The app shows the site in a web view. Google refuses to sign people in
there, and Apple's own sheet only appears in a browser, so the app opens
the sign-in in the system's sign-in sheet: a browser over the app, with
cookies of its own. A session started there would be the sheet's, not the
app's. So, as native apps do with OAuth (RFC 8252, with the proof key of
RFC 7636):

1. The app makes a random `verifier` and opens the sheet on
   /auth/<provider>/start?app=ios&app_challenge=<SHA-256 of the verifier>.
   The challenge travels with the attempt, in its cookie (social.start).
2. Once the provider vouches for the person (social.identify), the server
   starts no session: it makes a code (create) and sends the sheet to
   ch.rideandrail.app://signin?code=..., which closes it and hands the
   address to the app.
3. The app's web view posts the code and the verifier to /auth/app/finish
   (routes/app_sign_in.py), which starts the session there (use).

A code works once, for CODE_TTL, and only with the verifier its challenge
was made from: whoever else sees the code (another app answering to the same
kind of address) can't use it. Only its hash is stored.
"""
import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import database as db

# The apps that sign in this way, as they name themselves in ?app=.
APPS = ("ios",)
# Where the sign-in sheet is sent when it's done; the app's bundle identifier.
CALLBACK = "ch.rideandrail.app://signin"
CODE_TTL = timedelta(minutes=2)  # from the provider's answer to the app's request, with nobody typing
# SHA-256 as base64url without padding.
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_VERIFIER_LENGTH = 128  # RFC 7636


def is_challenge(value: str | None) -> bool:
    return bool(value) and bool(CHALLENGE.fullmatch(value))


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(user_id: int, challenge: str, language: str) -> str:
    """A code with which the app that made `challenge` can start a session
    for this user."""
    code = secrets.token_urlsafe(32)
    now = _now()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM app_sign_ins WHERE expires_at <= ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO app_sign_ins (code_hash, user_id, challenge, language, expires_at) VALUES (?, ?, ?, ?, ?)",
            (_hash(code), user_id, challenge, language, (now + CODE_TTL).isoformat()),
        )
    return code


def use(code: str | None, verifier: str | None) -> dict | None:
    """Use a code: {user_id, language} if it exists, hasn't expired and
    `verifier` is the one its challenge was made from; None otherwise. The
    code is gone either way: single use even if two requests race (only one
    DELETE can match), and a wrong verifier can't be tried again."""
    code, verifier = code or "", verifier or ""
    with db.get_conn() as conn:
        row = conn.execute(
            "DELETE FROM app_sign_ins WHERE code_hash = ? RETURNING user_id, challenge, language, expires_at",
            (_hash(code),),
        ).fetchone()
    if row is None or row["expires_at"] <= _now().isoformat() or len(verifier) > MAX_VERIFIER_LENGTH:
        return None
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(digest, row["challenge"]):
        return None
    return {"user_id": row["user_id"], "language": row["language"]}


def forget_expired() -> int:
    """Delete the codes nobody used in time (maintenance.py); returns how many."""
    with db.get_conn() as conn:
        return conn.execute("DELETE FROM app_sign_ins WHERE expires_at <= ?", (_now().isoformat(),)).rowcount
