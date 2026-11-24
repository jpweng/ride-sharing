"""
Sign-in with Google or Apple (social.py, routes/social.py), through HTTP
like the browser, against a stand-in for the provider: its token service
and its signing keys are replaced, and the ID tokens are really signed and
really checked. Isolated database, no network.
"""
import base64
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

import auth
import database as db
import main
import social

SITE = "https://testserver"
GOOGLE_ID = "google-client-id.apps.example"
APPLE_ID = "ch.rideandrail.signin"


def _pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


PROVIDER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
APPLE_OUR_KEY = ec.generate_private_key(ec.SECP256R1())


def _jwk(key, kid):
    return {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": kid, "alg": "RS256", "use": "sig"}


class Provider:
    """What the provider would answer: `claims` go into the ID token it
    returns for a code; `posted` is what our server sent to its token service."""

    def __init__(self):
        self.claims = {}
        self.posted = None
        self.status = 200
        self.signing_key = PROVIDER_KEY

    def id_token(self):
        return jwt.encode(self.claims, _pem(self.signing_key), algorithm="RS256", headers={"kid": "key-1"})

    def post(self, url, data=None, timeout=None):
        self.posted = {"url": url, **data}

        class Response:
            status_code = self.status

            def json(inner):
                return {"id_token": self.id_token()} if self.status == 200 else {"error": "invalid_grant"}
        return Response()


@pytest.fixture
def provider(monkeypatch):
    fake = Provider()
    monkeypatch.setattr(social.requests, "post", fake.post)
    monkeypatch.setattr(social, "_jwks", lambda name, refresh=False: [_jwk(PROVIDER_KEY, "key-1")])
    return fake


@pytest.fixture
def google(monkeypatch, isolated_db, provider):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", GOOGLE_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://rideandrail.example")
    return provider


@pytest.fixture
def apple(monkeypatch, isolated_db, provider):
    monkeypatch.setenv("APPLE_CLIENT_ID", APPLE_ID)
    monkeypatch.setenv("APPLE_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APPLE_KEY_ID", "KEY1234567")
    monkeypatch.setenv("APPLE_PRIVATE_KEY", _pem(APPLE_OUR_KEY))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://rideandrail.example")
    return provider


@pytest.fixture
def client(isolated_db):
    return TestClient(main.app, base_url=SITE)


def _start(client, name, lang="en"):
    """Leave for the provider: the parameters it was sent."""
    r = client.get(f"/auth/{name}/start?lang={lang}", follow_redirects=False)
    assert r.status_code == 302, r.text
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}, r


def _claims(sent, issuer, audience, **more):
    now = int(time.time())
    return {
        "iss": issuer, "aud": audience, "sub": "person-1", "email": "Marie@Example.ch", "email_verified": True,
        "nonce": sent["nonce"], "iat": now, "exp": now + 600, **more,
    }


def _google_back(client, provider, sent=None, state=None, **claims):
    sent = sent or _start(client, "google")[0]
    provider.claims = _claims(sent, "https://accounts.google.com", GOOGLE_ID, **claims)
    return client.get(f"/auth/google/callback?code=the-code&state={state or sent['state']}", follow_redirects=False)


def _me(client):
    return client.get("/api/auth/me").json()["user"]


def _error(response):
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query).get("signin_error", [None])[0]


# --- Switched on by its settings ---------------------------------------------------

def test_no_provider_without_settings(client):
    assert client.get("/api/auth/providers").json() == {"providers": []}
    for name in ("google", "apple", "facebook"):
        assert client.get(f"/auth/{name}/start", follow_redirects=False).status_code == 404
        assert client.get(f"/auth/{name}/callback?code=x&state=y", follow_redirects=False).status_code == 404
        assert client.post(f"/auth/{name}/callback", data={"code": "x"}, follow_redirects=False).status_code == 404


def test_each_provider_is_offered_once_all_its_settings_are_there(client, monkeypatch, google):
    assert client.get("/api/auth/providers").json() == {"providers": ["google"]}
    monkeypatch.setenv("APPLE_CLIENT_ID", APPLE_ID)
    monkeypatch.setenv("APPLE_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APPLE_KEY_ID", "KEY1234567")
    assert client.get("/api/auth/providers").json() == {"providers": ["google"]}  # the key is missing
    monkeypatch.setenv("APPLE_PRIVATE_KEY", _pem(APPLE_OUR_KEY))
    assert client.get("/api/auth/providers").json() == {"providers": ["google", "apple"]}
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "  ")
    assert client.get("/api/auth/providers").json() == {"providers": ["apple"]}


# --- Google ------------------------------------------------------------------------

def test_leaving_for_google(client, google):
    sent, response = _start(client, "google", lang="fr")
    assert response.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert sent["client_id"] == GOOGLE_ID
    assert sent["redirect_uri"] == "https://rideandrail.example/auth/google/callback"
    assert (sent["response_type"], sent["scope"]) == ("code", "openid email profile")
    assert sent["code_challenge_method"] == "S256" and len(sent["code_challenge"]) == 43
    assert len(sent["state"]) >= 20 and len(sent["nonce"]) >= 20
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-oauth=")
    for part in ("HttpOnly", "Secure", "SameSite=none", "Max-Age=600", "Path=/"):
        assert part.lower() in cookie.lower(), part


def test_a_first_sign_in_with_google_creates_the_account(client, google):
    sent, _ = _start(client, "google", lang="fr")
    back = _google_back(client, google, sent, given_name="Marie", family_name="Muster")

    assert back.status_code == 303
    assert back.headers["location"] == "/?lang=fr#/ride"
    # Our server proved itself to Google, with the PKCE verifier of this attempt.
    assert google.posted["url"] == "https://oauth2.googleapis.com/token"
    assert (google.posted["code"], google.posted["client_secret"]) == ("the-code", "google-secret")
    assert google.posted["redirect_uri"] == "https://rideandrail.example/auth/google/callback"
    digest = __import__("hashlib").sha256(google.posted["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == sent["code_challenge"]

    me = _me(client)
    assert me["email"] == "marie@example.ch"
    assert me["name"] is None and me["profile_complete"] is False
    assert me["suggested_name"] == {"first_name": "Marie", "last_name": "Muster"}
    assert db.get_user_by_email("marie@example.ch")["language"] == "fr"

    # The profile page as after an email link; then the suggestion is gone.
    r = client.post("/api/auth/profile", json={"first_name": "Marie", "last_name": "Muster", "adult": True, "accept_conduct": True})
    assert r.json()["user"]["name"] == "Marie M."
    assert "suggested_name" not in _me(client)


def test_signing_in_again_finds_the_same_account(client, google):
    _google_back(client, google)
    first = db.get_user_by_email("marie@example.ch")
    # Even if the address at Google has changed since.
    other_browser = TestClient(main.app, base_url=SITE)
    assert _google_back(other_browser, google, email="marie.new@example.ch").status_code == 303
    assert _me(other_browser)["email"] == "marie@example.ch"
    assert db.get_user_by_email("marie.new@example.ch") is None
    with db.get_conn() as conn:
        rows = conn.execute("SELECT user_id, provider, subject FROM user_identities").fetchall()
    assert [tuple(r) for r in rows] == [(first["id"], "google", "person-1")]


def test_an_account_made_by_email_link_is_linked_by_its_verified_address(client, google):
    existing = db.create_account("marie@example.ch", "~placeholder", "de")
    _google_back(client, google)
    assert _me(client)["email"] == "marie@example.ch"
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE email IS NOT NULL").fetchone()[0] == 1
        assert conn.execute("SELECT user_id FROM user_identities").fetchone()[0] == existing["id"]


def test_an_unverified_address_isnt_trusted(client, google):
    db.create_account("marie@example.ch", "~placeholder", "de")
    assert _error(_google_back(client, google, email_verified=False)) == "social_email_unverified"
    assert _me(client) is None
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_identities").fetchone()[0] == 0


def test_a_banned_account_cant_sign_in(client, google):
    db.create_account("marie@example.ch", "~placeholder", "de")
    db.set_banned("marie@example.ch", True)
    assert _error(_google_back(client, google)) == "account_banned"
    assert _me(client) is None


def test_the_answer_must_belong_to_a_sign_in_started_in_this_browser(client, google):
    sent, _ = _start(client, "google")
    # Another state than the one this browser was given.
    assert _error(_google_back(client, google, sent, state="someone-elses-state")) == "social_failed"
    assert _me(client) is None
    # No cookie at all: the link was opened in another browser.
    elsewhere = TestClient(main.app, base_url=SITE)
    google.claims = _claims(sent, "https://accounts.google.com", GOOGLE_ID)
    r = elsewhere.get(f"/auth/google/callback?code=the-code&state={sent['state']}", follow_redirects=False)
    assert _error(r) == "social_failed"
    assert google.posted is None  # the code was never sent to Google


def test_an_attempt_expires_and_is_used_once(client, google, monkeypatch):
    sent, _ = _start(client, "google")
    real_time = time.time
    monkeypatch.setattr(social.time, "time", lambda: real_time() + social.STATE_TTL_S + 1)
    assert _error(_google_back(client, google, sent)) == "social_failed"
    monkeypatch.setattr(social.time, "time", real_time)

    sent, _ = _start(client, "google")
    assert _error(_google_back(client, google, sent)) is None
    # The cookie is gone: the same answer again is refused.
    client.post("/api/auth/sign_out")
    assert _error(_google_back(client, google, sent)) == "social_failed"
    assert _me(client) is None


@pytest.mark.parametrize("claims", [
    {"nonce": "another-nonce"},                 # not the answer to this sign-in
    {"aud": "another-app"},                     # a token made for someone else
    {"iss": "https://accounts.evil.example"},   # not from Google
    {"exp": int(time.time()) - 3600},           # expired
    {"sub": None},
])
def test_an_id_token_that_doesnt_check_out(client, google, claims):
    assert _error(_google_back(client, google, **claims)) == "social_failed"
    assert _me(client) is None


def test_an_id_token_signed_by_someone_else(client, google):
    google.signing_key = OTHER_KEY
    assert _error(_google_back(client, google)) == "social_failed"
    assert _me(client) is None


def test_google_refusing_the_code(client, google):
    google.status = 400
    assert _error(_google_back(client, google)) == "social_failed"


def test_saying_no_at_the_provider(client, google):
    _start(client, "google", lang="it")
    r = client.get("/auth/google/callback?error=access_denied&state=x", follow_redirects=False)
    assert r.headers["location"] == "/?lang=it&signin_error=social_cancelled#/ride"
    r = client.get("/auth/google/callback?error=server_error", follow_redirects=False)
    assert _error(r) == "social_failed"


def test_signing_in_replaces_the_browsers_previous_session(client, google):
    _google_back(client, google)
    first = client.cookies.get("__Host-session")
    _google_back(client, google, sub="person-2", email="paul@example.ch")
    assert _me(client)["email"] == "paul@example.ch"
    assert auth.session_for(first) is None


# --- Apple -------------------------------------------------------------------------

def _apple_back(client, provider, sent=None, form=None, **claims):
    sent = sent or _start(client, "apple")[0]
    provider.claims = _claims(sent, "https://appleid.apple.com", APPLE_ID, email_verified="true", **claims)
    return client.post("/auth/apple/callback", data={"code": "the-code", "state": sent["state"], **(form or {})},
                       follow_redirects=False)


def test_leaving_for_apple(client, apple):
    sent, response = _start(client, "apple")
    assert response.headers["location"].startswith("https://appleid.apple.com/auth/authorize?")
    assert (sent["client_id"], sent["scope"], sent["response_mode"]) == (APPLE_ID, "name email", "form_post")
    assert sent["redirect_uri"] == "https://rideandrail.example/auth/apple/callback"
    assert "code_challenge" not in sent
    # Apple's form is posted from its site: a Lax cookie wouldn't come with it.
    assert "samesite=none" in response.headers["set-cookie"].lower()


def test_a_first_sign_in_with_apple(client, apple):
    user = json.dumps({"name": {"firstName": "Lea", "lastName": "Rossi"}, "email": "lea@example.ch"})
    back = _apple_back(client, apple, form={"user": user}, email="lea@example.ch")
    assert back.status_code == 303 and _error(back) is None

    # Our client secret: a short-lived token signed with our key, naming our team and Services ID.
    assert apple.posted["url"] == "https://appleid.apple.com/auth/token"
    assert "code_verifier" not in apple.posted
    secret = apple.posted["client_secret"]
    assert jwt.get_unverified_header(secret) == {"alg": "ES256", "kid": "KEY1234567", "typ": "JWT"}
    said = jwt.decode(secret, APPLE_OUR_KEY.public_key(), algorithms=["ES256"], audience="https://appleid.apple.com")
    assert (said["iss"], said["sub"]) == ("TEAM123456", APPLE_ID)
    assert 0 < said["exp"] - said["iat"] <= 600

    me = _me(client)
    assert me["email"] == "lea@example.ch"
    assert me["suggested_name"] == {"first_name": "Lea", "last_name": "Rossi"}


def test_apples_key_can_be_given_base64_encoded(client, apple, monkeypatch):
    monkeypatch.setenv("APPLE_PRIVATE_KEY", base64.b64encode(_pem(APPLE_OUR_KEY).encode()).decode())
    assert _error(_apple_back(client, apple)) is None
    monkeypatch.setenv("APPLE_PRIVATE_KEY", "not a key")
    other = TestClient(main.app, base_url=SITE)
    assert _error(_apple_back(other, apple)) == "social_failed"


def test_apple_and_google_with_the_same_address_are_one_account(client, apple, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", GOOGLE_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-secret")
    _apple_back(client, apple, sub="apple-person")
    _google_back(TestClient(main.app, base_url=SITE), apple, sub="google-person")
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE email = 'marie@example.ch'").fetchone()[0] == 1
        assert sorted(r[0] for r in conn.execute("SELECT provider FROM user_identities")) == ["apple", "google"]


def test_saying_no_at_apple(client, apple):
    _start(client, "apple")
    r = client.post("/auth/apple/callback", data={"error": "user_cancelled_authorize", "state": "x"}, follow_redirects=False)
    assert _error(r) == "social_cancelled"


# --- The account -------------------------------------------------------------------

def test_deleting_the_account_forgets_its_identities(client, google):
    _google_back(client, google)
    user = db.get_user_by_email("marie@example.ch")
    assert db.delete_account(user["id"])["identities"] == 1
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_identities").fetchone()[0] == 0
    # Signing in again starts afresh.
    again = TestClient(main.app, base_url=SITE)
    _google_back(again, google)
    assert _me(again)["name"] is None


def test_the_page_shows_a_message_only_for_the_codes_the_server_sends():
    """The code is read from the address: a known one has its own message,
    anything else is "it didn't work". Nothing is called by its name."""
    script = (Path(__file__).resolve().parents[2] / "frontend" / "social.js").read_text()
    show = script[script.index("function showSocialSignInError() {"):script.index("function suggestProfileName() {")]
    for known in ("social_cancelled", "social_email_unverified", "account_banned"):
        assert f'code === "{known}" ? t("errors.{known}")' in show
    assert ': t("errors.social_failed");' in show
    assert "[code]" not in show and "messages" not in show
    # Each of those codes, and the fallback, has its text in every language.
    for lang in ("en", "de", "fr", "it"):
        texts = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "i18n" / lang / "errors.json").read_text())
        for code in ("social_cancelled", "social_email_unverified", "account_banned", "social_failed"):
            assert texts[f"errors.{code}"], (lang, code)
