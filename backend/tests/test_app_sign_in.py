"""
Sign-in with Google or Apple from the iOS app (app_sign_in.py,
routes/app_sign_in.py): the sign-in sheet and the app's web view are two
browsers with cookies of their own, played here by two clients; the
provider is test_social.py's stand-in. Isolated database, no network.
"""
import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import app_sign_in
import auth
import database as db
import main
import maintenance
from test_social import (  # noqa: F401 (fixtures)
    APPLE_ID, GOOGLE_ID, SITE, _claims, _me, apple, client, google, provider,
)

VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # RFC 7636's example


def _challenge(verifier=VERIFIER):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture
def sheet(isolated_db):
    """The system's sign-in sheet: a browser that isn't the app's web view."""
    return TestClient(main.app, base_url=SITE)


def _start(sheet, name="google", lang="en", challenge=None):
    r = sheet.get(f"/auth/{name}/start?lang={lang}&app=ios&app_challenge={challenge or _challenge()}",
                  follow_redirects=False)
    assert r.status_code == 302, r.text
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}


def _answer(response):
    """What the sheet ends on: the app's own kind of address, and what it carries."""
    assert response.status_code == 303
    address = urlsplit(response.headers["location"])
    assert (address.scheme, address.netloc, address.path) == ("ch.rideandrail.app", "signin", "")
    return {k: v[0] for k, v in parse_qs(address.query).items()}


def _google_back(sheet, provider, lang="en", challenge=None, **claims):
    sent = _start(sheet, "google", lang, challenge)
    provider.claims = _claims(sent, "https://accounts.google.com", GOOGLE_ID, **claims)
    return sheet.get(f"/auth/google/callback?code=the-code&state={sent['state']}", follow_redirects=False)


def _finish(app, code, verifier=VERIFIER, lang=None):
    form = {"code": code, "verifier": verifier, **({"lang": lang} if lang else {})}
    return app.post("/auth/app/finish", data=form, follow_redirects=False)


# --- From the sheet into the app ---------------------------------------------------

def test_the_app_is_signed_in_not_the_sheet(client, sheet, google):
    answer = _answer(_google_back(sheet, google, lang="fr", given_name="Marie", family_name="Muster"))
    assert set(answer) == {"code", "lang"} and answer["lang"] == "fr"
    # The sheet got no session: it is not where the person uses the site.
    assert "__Host-session" not in sheet.cookies
    assert _me(sheet) is None
    # The attempt's cookie is cleared there, as after any sign-in.
    assert "__Host-oauth" not in sheet.cookies

    back = _finish(client, answer["code"])
    assert back.status_code == 303 and back.headers["location"] == "/?lang=fr#/ride"
    cookie = back.headers["set-cookie"]
    for part in ("__Host-session=", "HttpOnly", "Secure", "SameSite=lax"):
        assert part.lower() in cookie.lower(), part
    me = _me(client)
    assert me["email"] == "marie@example.ch"
    # A first sign-in goes through the profile page, as in a browser.
    assert me["profile_complete"] is False
    assert me["suggested_name"] == {"first_name": "Marie", "last_name": "Muster"}


def test_with_apple(client, sheet, apple):
    sent = _start(sheet, "apple", lang="de")
    apple.claims = _claims(sent, "https://appleid.apple.com", APPLE_ID, email_verified="true", email="lea@example.ch")
    back = sheet.post("/auth/apple/callback", data={"code": "the-code", "state": sent["state"]}, follow_redirects=False)
    answer = _answer(back)
    assert _finish(client, answer["code"]).headers["location"] == "/?lang=de#/ride"
    assert _me(client)["email"] == "lea@example.ch"


def test_the_code_itself_is_never_stored_and_works_once(client, sheet, google):
    code = _answer(_google_back(sheet, google))["code"]
    assert len(code) >= 40
    with db.get_conn() as conn:
        stored = [tuple(row) for row in conn.execute("SELECT * FROM app_sign_ins")]
    assert len(stored) == 1 and code not in stored[0]

    assert _finish(client, code).headers["location"] == "/?lang=en#/ride"
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM app_sign_ins").fetchone()[0] == 0
    # The same code again, in another web view: refused.
    other = TestClient(main.app, base_url=SITE)
    assert _finish(other, code).headers["location"] == "/?signin_error=social_failed#/ride"
    assert _me(other) is None


def test_a_code_is_useless_without_the_verifier_it_was_made_for(client, sheet, google):
    code = _answer(_google_back(sheet, google))["code"]
    assert _finish(client, code, verifier="someone-elses-verifier").headers["location"] == "/?signin_error=social_failed#/ride"
    assert _me(client) is None
    # And it can't be tried again, even with the right one.
    assert "signin_error=social_failed" in _finish(client, code).headers["location"]
    assert _me(client) is None


@pytest.mark.parametrize("form", [
    {},
    {"code": "", "verifier": ""},
    {"code": "no-such-code", "verifier": VERIFIER},
    {"verifier": VERIFIER},
])
def test_what_isnt_a_code(client, sheet, google, form):
    _google_back(sheet, google)  # a real code exists, and isn't this one
    r = client.post("/auth/app/finish", data={**form, "lang": "it"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?lang=it&signin_error=social_failed#/ride"
    assert _me(client) is None


def test_a_code_expires(client, sheet, google, monkeypatch):
    code = _answer(_google_back(sheet, google))["code"]
    real_now = app_sign_in._now
    monkeypatch.setattr(app_sign_in, "_now", lambda: real_now() + app_sign_in.CODE_TTL + timedelta(seconds=1))
    assert "signin_error=social_failed" in _finish(client, code).headers["location"]
    assert _me(client) is None


def test_codes_nobody_used_are_deleted(sheet, google, monkeypatch):
    _google_back(sheet, google)
    _google_back(sheet, google, sub="person-2", email="paul@example.ch")
    assert app_sign_in.forget_expired() == 0
    real_now = app_sign_in._now
    monkeypatch.setattr(app_sign_in, "_now", lambda: real_now() + app_sign_in.CODE_TTL + timedelta(seconds=1))
    # By the hourly upkeep...
    assert maintenance.purge_old_data(maintenance.datetime(2026, 11, 24, 12, 0))["app_sign_ins"] == 2
    # ... and by the next sign-in from the app.
    monkeypatch.setattr(app_sign_in, "_now", real_now)
    _google_back(sheet, google)
    monkeypatch.setattr(app_sign_in, "_now", lambda: real_now() + app_sign_in.CODE_TTL + timedelta(seconds=1))
    _google_back(sheet, google)
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM app_sign_ins").fetchone()[0] == 1


def test_signing_in_replaces_the_apps_previous_session(client, sheet, google):
    assert _finish(client, _answer(_google_back(sheet, google))["code"]).status_code == 303
    first = client.cookies.get("__Host-session")
    again = _answer(_google_back(sheet, google, sub="person-2", email="paul@example.ch"))["code"]
    _finish(client, again)
    assert _me(client)["email"] == "paul@example.ch"
    assert auth.session_for(first) is None


def test_the_sheets_own_session_is_left_alone(client, sheet, google):
    # The person is signed in on the site in Safari, which the sheet shares its cookies with.
    r = sheet.get("/auth/google/start", follow_redirects=False)
    sent = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    google.claims = _claims(sent, "https://accounts.google.com", GOOGLE_ID, sub="safari-person", email="safari@example.ch")
    sheet.get(f"/auth/google/callback?code=the-code&state={sent['state']}", follow_redirects=False)
    assert _me(sheet)["email"] == "safari@example.ch"

    _finish(client, _answer(_google_back(sheet, google))["code"])
    assert _me(client)["email"] == "marie@example.ch"
    assert _me(sheet)["email"] == "safari@example.ch"


# --- When it fails, the app is told ------------------------------------------------

def test_saying_no_at_the_provider(sheet, google):
    _start(sheet, lang="it")
    r = sheet.get("/auth/google/callback?error=access_denied&state=x", follow_redirects=False)
    assert _answer(r) == {"error": "social_cancelled", "lang": "it"}


def test_a_failed_sign_in(sheet, google):
    google.status = 400  # Google refuses the code
    assert _answer(_google_back(sheet, google, lang="de")) == {"error": "social_failed", "lang": "de"}
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM app_sign_ins").fetchone()[0] == 0


def test_a_banned_account(client, sheet, google):
    db.create_account("marie@example.ch", "~placeholder", "de")
    db.set_banned("marie@example.ch", True)
    assert _answer(_google_back(sheet, google))["error"] == "account_banned"


def test_an_account_closed_or_deleted_before_the_app_asks(client, sheet, google):
    code = _answer(_google_back(sheet, google))["code"]
    db.set_banned("marie@example.ch", True)
    assert _finish(client, code).headers["location"] == "/?lang=en&signin_error=account_banned#/ride"
    assert _me(client) is None

    db.set_banned("marie@example.ch", False)
    code = _answer(_google_back(sheet, google))["code"]
    # Deleting the account takes its codes with it.
    db.delete_account(db.get_user_by_email("marie@example.ch")["id"])
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM app_sign_ins").fetchone()[0] == 0
    assert "signin_error=social_failed" in _finish(client, code).headers["location"]


# --- Starting ----------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "app=ios",                                            # no challenge
    "app=ios&app_challenge=short",
    "app=ios&app_challenge=" + "a" * 44,                  # not a SHA-256 in base64url
    "app=ios&app_challenge=" + "a" * 42 + "%2B",          # "+" isn't base64url
    "app=android&app_challenge=" + _challenge(),          # not an app that signs in this way
])
def test_an_app_must_say_which_it_is_and_give_a_challenge(sheet, google, query):
    # Not sent to the provider: straight back to the app, which shows that it failed.
    r = sheet.get(f"/auth/google/start?lang=fr&{query}", follow_redirects=False)
    assert _answer(r) == {"error": "social_failed", "lang": "fr"}
    assert "__Host-oauth" not in sheet.cookies


def test_a_challenge_alone_changes_nothing(sheet, google):
    # Without app=, it is a sign-in in a browser, which ends on the site with a session.
    r = sheet.get(f"/auth/google/start?app_challenge={_challenge()}", follow_redirects=False)
    sent = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    google.claims = _claims(sent, "https://accounts.google.com", GOOGLE_ID)
    back = sheet.get(f"/auth/google/callback?code=the-code&state={sent['state']}", follow_redirects=False)
    assert back.headers["location"] == "/?lang=en#/ride"
    assert _me(sheet)["email"] == "marie@example.ch"


def test_the_provider_isnt_told_about_the_app(sheet, google):
    sent = _start(sheet)
    assert "app" not in sent and "app_challenge" not in sent
    assert sent["redirect_uri"] == "https://rideandrail.example/auth/google/callback"
