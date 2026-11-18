"""
Sign-in by a one-time link sent by email, and sessions. routes/auth.py and
api.py turn this into HTTP: the endpoints, the session cookie and the checks
on every route.

1. request_link: the user enters their email; a link with a random token is
   emailed (mailer.py). Only the token's SHA-256 is stored; it works once,
   for TOKEN_TTL. The answer is the same whether the email has an account
   or not, and the same when no email is sent because of the per-email
   limit or a ban, so nobody can find out who has an account.
2. verify: opening the link (the page POSTs the token, see app.js) marks it
   used and starts a session, creating the account on first sign-in.
3. complete_profile: on first sign-in, the user gives their first and last
   name, confirms they are 18 or older and accepts the code of conduct
   (frontend/conduct.html, CONDUCT_VERSION). Until then they can't do
   anything else. A new CONDUCT_VERSION asks everyone to accept it again.
4. Sessions: a random token in an HttpOnly, Secure, SameSite=Lax cookie;
   only its hash is stored. They last SESSION_TTL from the last visit
   (sliding, extended at most once a day), until sign-out, or until the
   user is banned or deletes their account.

Demo: "view as a demo driver" starts a read-only session as one of the
fictional drivers of demo_drives.py, without email (demo_session). Demo
drivers have no email address, so they never receive email.
"""
import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import database as db
import demo_drives
import mailer

TOKEN_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=90)
SESSION_EXTEND_AFTER = timedelta(days=1)
DEMO_SESSION_TTL = timedelta(days=1)
SIGN_IN_LINK_RETENTION = timedelta(days=1)  # then the rows the limits count are deleted

# Sign-in links per email address (beyond these, no email is sent, silently)
# and per IP address (beyond these, a 429 error): (window, max).
LINKS_PER_EMAIL = [(timedelta(minutes=15), 3), (timedelta(days=1), 10)]
LINKS_PER_IP = [(timedelta(minutes=15), 10), (timedelta(days=1), 50)]

# The code of conduct's version (frontend/conduct.html). Changing it asks
# every user to accept the new one at their next visit.
CONDUCT_VERSION = "2026-11-draft"

LANGUAGES = mailer.LANGUAGES
MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 50
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")

# How many demo drivers "view as a demo driver" offers.
DEMO_DRIVERS_OFFERED = 8


class AuthError(Exception):
    """An error with a code the API returns (see api.api_error)."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_RE.match(email):
        raise AuthError(400, "invalid_email", "Enter a valid email address")
    return email


# ---------------------------------------------------------------------
# Sign-in links
# ---------------------------------------------------------------------
def request_link(email: str, language: str, ip: str | None, base_url: str) -> bool:
    """Email a sign-in link to `email`, within the limits; returns whether
    an email was sent. The caller answers the same either way."""
    email = normalize_email(email)
    language = language if language in LANGUAGES else "en"
    now = _now()

    if ip is not None:
        for window, limit in LINKS_PER_IP:
            if db.count_sign_in_links(_iso(now - window), ip=ip) >= limit:
                raise AuthError(429, "too_many_requests", "Too many sign-in links asked for; try again later")

    user = db.get_user_by_email(email)
    over_limit = any(
        db.count_sign_in_links(_iso(now - window), email=email, sent_only=True) >= limit
        for window, limit in LINKS_PER_EMAIL
    )
    # Demo drivers have no email, so `user` is never one; checked anyway.
    if over_limit or (user and (user["banned_at"] or user["is_demo"])):
        db.add_sign_in_link(None, email, ip, language, _iso(now))
        return False

    token = secrets.token_urlsafe(32)
    db.add_sign_in_link(hash_token(token), email, ip, language, _iso(now + TOKEN_TTL))
    # ?lang= opens the page in the language the link was asked for in.
    link = f"{base_url.rstrip('/')}/?lang={language}#/signin/{token}"
    try:
        mailer.send_sign_in_link(email, language, link, int(TOKEN_TTL.total_seconds() // 60))
    except mailer.EmailError:
        db.delete_sign_in_link(hash_token(token))
        raise
    return True


def verify(token: str) -> tuple[dict, str]:
    """Use a sign-in link's token: (user, session token). Creates the
    account on first sign-in."""
    link = db.use_sign_in_link(hash_token(token or ""), _iso(_now()))
    if link is None:
        raise AuthError(400, "invalid_link", "This sign-in link is invalid, expired or already used")
    user = db.get_user_by_email(link["email"])
    if user is None:
        try:
            # The name is chosen next (complete_profile); until then nobody sees it.
            user = db.create_account(link["email"], f"~{secrets.token_hex(8)}", link["language"])
        except sqlite3.IntegrityError:  # created by a concurrent sign-in
            user = db.get_user_by_email(link["email"])
    if user["banned_at"]:
        raise AuthError(403, "account_banned", "This account is closed")
    db.set_user_language(user["id"], link["language"])
    return db.get_user(user["id"]), _start_session(user["id"], is_demo=False)


def _start_session(user_id: int, is_demo: bool) -> str:
    token = secrets.token_urlsafe(32)
    ttl = DEMO_SESSION_TTL if is_demo else SESSION_TTL
    db.create_session(hash_token(token), user_id, is_demo, _iso(_now() + ttl))
    return token


def start_session(user_id: int) -> str:
    """A session for a user who has just proved who they are another way
    than by the email link (social.py): its token."""
    return _start_session(user_id, is_demo=False)


# ---------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------
def session_for(token: str | None) -> dict | None:
    """The live session for a cookie value: {"session", "user", "extended"},
    or None (no such session, expired, or the user is banned). Extends it
    (SESSION_TTL from now) at most once every SESSION_EXTEND_AFTER;
    "extended" tells the caller to send the cookie again."""
    if not token:
        return None
    found = db.get_session(hash_token(token))
    if found is None:
        return None
    session, user = found["session"], found["user"]
    now = _now()
    if session["expires_at"] <= _iso(now):
        db.delete_session(session["token_hash"])
        return None
    if user["banned_at"]:
        db.delete_user_sessions(user["id"])
        return None
    extended = False
    if not session["is_demo"] and now - datetime.fromisoformat(session["last_seen_at"]) >= SESSION_EXTEND_AFTER:
        db.extend_session(session["id"], _iso(now), _iso(now + SESSION_TTL))
        extended = True
    return {"session": session, "user": user, "extended": extended}


def end_session(token: str | None) -> None:
    if token:
        db.delete_session(hash_token(token))


# ---------------------------------------------------------------------
# Profile: name, 18+, code of conduct
# ---------------------------------------------------------------------
def profile_complete(user: dict) -> bool:
    if user["is_demo"]:
        return True
    return bool(user["first_name"] and user["adult_confirmed_at"] and user["conduct_version"] == CONDUCT_VERSION)


def _display_name(first_name: str, last_name: str) -> str:
    """First name and last initial, unique: "Marie M.", else "Marie M. 2"..."""
    base = f"{first_name} {last_name[0].upper()}."
    name, n = base, 1
    while db.name_taken(name):
        n += 1
        name = f"{base} {n}"
    return name


def _clean_name(value: str | None) -> str:
    return " ".join((value or "").split())[:MAX_NAME_LENGTH]


def complete_profile(user: dict, first_name: str | None, last_name: str | None,
                     adult: bool, accept_conduct: bool) -> dict:
    """Record the user's name (first sign-in only), their confirmation that
    they're 18 or older and their acceptance of the code of conduct."""
    if user["is_demo"]:
        raise AuthError(403, "demo_read_only", "Demo drivers are read-only")
    if not (adult and accept_conduct):
        raise AuthError(400, "consent_required", "Confirm you are 18 or older and accept the code of conduct")
    if user["first_name"]:
        return db.set_profile(user["id"], CONDUCT_VERSION)  # the name stays
    first, last = _clean_name(first_name), _clean_name(last_name)
    if not first or not last:
        raise AuthError(400, "name_required", "Enter your first and last name")
    for _ in range(5):  # another account may take the same name meanwhile
        try:
            return db.set_profile(user["id"], CONDUCT_VERSION, _display_name(first, last), first, last)
        except sqlite3.IntegrityError:
            continue
    raise AuthError(409, "name_taken", "Try again")


def public_user(user: dict, session: dict | None = None) -> dict:
    """What the signed-in user's own page needs to know about them."""
    complete = profile_complete(user)
    return {
        "name": user["name"] if (user["first_name"] or user["is_demo"]) else None,
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "email": user["email"],
        "is_demo": bool(user["is_demo"]),
        "profile_complete": complete,
        # Signed in before but the code of conduct changed: accept it again.
        "needs_conduct": not complete and bool(user["first_name"]),
        "conduct_version": CONDUCT_VERSION,
    }


# ---------------------------------------------------------------------
# Demo drivers
# ---------------------------------------------------------------------
def demo_drivers() -> list[str]:
    """The demo drivers "view as a demo driver" offers, Jean Dupont first."""
    names = []
    for spec in demo_drives.DEMO_DRIVES:
        if spec["driver_name"] not in names:
            names.append(spec["driver_name"])
        if len(names) >= DEMO_DRIVERS_OFFERED:
            break
    return names


def demo_session(driver_name: str) -> tuple[dict, str]:
    """A read-only session as a fictional demo driver: (user, token)."""
    if driver_name not in {spec["driver_name"] for spec in demo_drives.DEMO_DRIVES}:
        raise AuthError(404, "demo_driver_not_found", "No such demo driver")
    user_id = db.get_or_create_user(driver_name, is_demo=True)
    user = db.get_user(user_id)
    if not user["is_demo"] or user["email"]:  # never a real account
        raise AuthError(404, "demo_driver_not_found", "No such demo driver")
    return user, _start_session(user_id, is_demo=True)
