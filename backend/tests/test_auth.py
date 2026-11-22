"""
Sign-in by email link, sessions and who may do what (auth.py, api.py, routes/auth.py),
through HTTP like the browser: the session cookie, the checks in front of
every route, cross-site requests, rate limits, bans, the demo-driver view
and account deletion. Isolated database, emails kept in memory (the console
backend), no network.
"""
import hashlib
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import database as db
import mailer
import main
from routes import auth as auth_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
IN_TWO_DAYS = (date.today() + timedelta(days=2)).isoformat()
VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
FRIBOURG = {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619}
SITE = "https://testserver"


@pytest.fixture
def outbox(monkeypatch):
    backend = mailer.ConsoleBackend()
    monkeypatch.setattr(mailer, "_backend", backend)
    return backend.outbox


@pytest.fixture
def new_client(isolated_db, outbox, force_haversine, fake_transit):
    """A browser: its own cookies. HTTPS, so the Secure cookie is sent back."""
    return lambda: TestClient(main.app, base_url=SITE)


@pytest.fixture
def client(new_client):
    return new_client()


def _token(email: dict) -> str:
    return re.search(r"#/signin/([\w-]+)", email["text"]).group(1)


def _ask_link(client, email, language="en"):
    return client.post("/api/auth/link", json={"email": email, "language": language})


def _sign_in(client, outbox, email, first="Marie", last="Muster"):
    """Sign in by email link and complete the profile; returns the user."""
    assert _ask_link(client, email).json() == {"ok": True}
    assert client.post("/api/auth/verify", json={"token": _token(outbox[-1])}).status_code == 200
    r = client.post("/api/auth/profile", json={
        "first_name": first, "last_name": last, "adult": True, "accept_conduct": True,
    })
    assert r.status_code == 200, r.json()
    return r.json()["user"]


def _drive(client, day=TOMORROW, at="18:00"):
    r = client.post("/api/drives", json={"origin": VEVEY, "destination": FRIBOURG, "departure_time": f"{day}T{at}"})
    assert r.status_code == 200, r.json()
    return r.json()


def _ride_request(client, day=IN_TWO_DAYS, at="18:30"):
    r = client.post("/api/requests", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{day}T{at}"})
    assert r.status_code == 200, r.json()
    return r.json()["request"]


def _code(response):
    return response.json()["detail"]["code"]


def _email_code(email: dict) -> str:
    """The code a sign-in email carries, next to its link."""
    return re.search(r": (\d{6})$", email["text"], re.M).group(1)


def _type_code(client, email, code):
    return client.post("/api/auth/code", json={"email": email, "code": code})


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


# ---------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------
def test_sign_in_flow(client, outbox):
    assert client.get("/api/auth/me").json() == {"user": None}

    assert _ask_link(client, "  Marie.Muster@Example.CH ", "fr").json() == {"ok": True}
    email = outbox[-1]
    assert email["to"] == "marie.muster@example.ch"
    assert email["subject"] == mailer.text("fr", "email.signIn.subject")
    assert f"{auth_routes.PUBLIC_BASE_URL}/?lang=fr#/signin/" in email["text"]

    r = client.post("/api/auth/verify", json={"token": _token(email)})
    user = r.json()["user"]
    assert (user["email"], user["name"], user["profile_complete"]) == ("marie.muster@example.ch", None, False)

    # Nothing else until the profile is complete.
    assert _code(client.get("/api/drives?mine=true")) == "profile_incomplete"
    no_consent = client.post("/api/auth/profile", json={"first_name": "Marie", "last_name": "Muster", "adult": True})
    assert _code(no_consent) == "consent_required"
    r = client.post("/api/auth/profile", json={
        "first_name": " Marie ", "last_name": "müller", "adult": True, "accept_conduct": True,
    })
    assert r.json()["user"]["name"] == "Marie M."  # all other users ever see

    me = client.get("/api/auth/me").json()["user"]
    assert me["profile_complete"] and me["first_name"] == "Marie"
    stored = db.get_user_by_email("marie.muster@example.ch")
    assert stored["adult_confirmed_at"] and stored["conduct_accepted_at"]
    assert stored["conduct_version"] == auth.CONDUCT_VERSION
    assert stored["language"] == "fr"
    assert client.get("/api/drives?mine=true").status_code == 200


def test_the_answer_doesnt_tell_who_has_an_account(new_client, outbox):
    _sign_in(new_client(), outbox, "known@example.ch")
    other = new_client()
    known, unknown = _ask_link(other, "known@example.ch"), _ask_link(other, "unknown@example.ch")
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json()) == (200, {"ok": True})
    assert [e["to"] for e in outbox[-2:]] == ["known@example.ch", "unknown@example.ch"]


def test_invalid_email(client, outbox):
    assert _code(_ask_link(client, "not an email")) == "invalid_email"
    assert outbox == []


def test_token_is_stored_hashed_and_works_once(new_client, outbox):
    client = new_client()
    _ask_link(client, "a@example.ch")
    token = _token(outbox[-1])
    with sqlite3.connect(db.DB_PATH) as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM sign_in_links")]
    assert stored == [hashlib.sha256(token.encode()).hexdigest()]

    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
    again = new_client().post("/api/auth/verify", json={"token": token})
    assert (again.status_code, _code(again)) == (400, "invalid_link")
    assert _code(client.post("/api/auth/verify", json={"token": "made-up"})) == "invalid_link"


def test_token_expires(client, outbox):
    _ask_link(client, "a@example.ch")
    assert auth.TOKEN_TTL == timedelta(minutes=15)
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE sign_in_links SET expires_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    assert _code(client.post("/api/auth/verify", json={"token": _token(outbox[-1])})) == "invalid_link"


def test_the_emails_code_signs_in_where_the_link_was_asked_for(new_client, outbox):
    # The app installed on an iPhone: the link opens in Safari, which has
    # other cookies. The code is typed in the app itself.
    app = new_client()
    _ask_link(app, "Marie.Muster@example.ch", "fr")
    code = _email_code(outbox[-1])
    assert auth.CODE_DIGITS == 6 and re.fullmatch(r"\d{6}", code)

    r = _type_code(app, " marie.muster@Example.ch ", f" {code[:3]} {code[3:]} ")  # as people type them
    assert r.status_code == 200, r.json()
    user = r.json()["user"]
    assert (user["email"], user["profile_complete"]) == ("marie.muster@example.ch", False)
    assert app.get("/api/auth/me").json()["user"]["email"] == "marie.muster@example.ch"
    assert db.get_user_by_email("marie.muster@example.ch")["language"] == "fr"
    cookie = r.headers["set-cookie"].lower()
    assert "__host-session=" in cookie and "httponly" in cookie


def test_a_link_and_its_code_are_one_sign_in(new_client, outbox):
    first, second = new_client(), new_client()
    _ask_link(first, "a@example.ch")
    code, token = _email_code(outbox[-1]), _token(outbox[-1])
    with sqlite3.connect(db.DB_PATH) as conn:  # stored hashed, like the token
        stored = [r[0] for r in conn.execute("SELECT code_hash FROM sign_in_links")]
    assert stored == [hashlib.sha256(f"a@example.ch:{code}".encode()).hexdigest()]

    assert _type_code(first, "a@example.ch", code).status_code == 200
    assert _code(_type_code(second, "a@example.ch", code)) == "invalid_code"  # works once
    assert _code(second.post("/api/auth/verify", json={"token": token})) == "invalid_link"  # and uses the link up

    _ask_link(first, "a@example.ch")
    code, token = _email_code(outbox[-1]), _token(outbox[-1])
    assert second.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert _code(_type_code(first, "a@example.ch", code)) == "invalid_code"  # the link uses the code up


def test_a_code_only_works_for_its_address_and_its_time(client, outbox):
    _ask_link(client, "a@example.ch")
    code = _email_code(outbox[-1])
    r = _type_code(client, "b@example.ch", code)
    assert (r.status_code, _code(r)) == (400, "invalid_code")
    assert _code(_type_code(client, "not an email", code)) == "invalid_email"
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE sign_in_links SET expires_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    assert _code(_type_code(client, "a@example.ch", code)) == "invalid_code"


def test_a_few_wrong_codes_and_only_the_link_still_works(new_client, outbox):
    client = new_client()
    _ask_link(client, "a@example.ch")
    code, token = _email_code(outbox[-1]), _token(outbox[-1])
    assert auth.CODE_ATTEMPTS == 5

    # What can't be a code isn't a guess.
    for not_a_code in ("12345", "1234567", "abcdef", "١٢٣٤٥٦"):
        assert _code(_type_code(client, "a@example.ch", not_a_code)) == "invalid_code"
    for _ in range(auth.CODE_ATTEMPTS - 1):
        assert _code(_type_code(client, "a@example.ch", _wrong(code))) == "invalid_code"
    # Wrong codes for another address don't count against this one.
    assert _code(_type_code(client, "b@example.ch", code)) == "invalid_code"
    assert _code(_type_code(client, "a@example.ch", _wrong(code))) == "invalid_code"  # the fifth

    assert _code(_type_code(client, "a@example.ch", code)) == "invalid_code"  # the right one, too late
    # A new link starts again, and the first link itself still works.
    _ask_link(client, "a@example.ch")
    assert _type_code(new_client(), "a@example.ch", _email_code(outbox[-1])).status_code == 200
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200


def test_no_code_signs_a_banned_account_in(new_client, outbox):
    client = new_client()
    _sign_in(client, outbox, "a@example.ch")
    _ask_link(client, "a@example.ch")
    code = _email_code(outbox[-1])
    assert db.set_banned("a@example.ch", True)
    r = _type_code(new_client(), "a@example.ch", code)
    assert (r.status_code, _code(r)) == (403, "account_banned")


def test_the_sign_in_page_takes_the_code():
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    html = (frontend / "index.html").read_text()
    page = html[html.index('<section id="page-signin"'):html.index('<section id="page-verify"')]
    form = re.search(r'<form id="signin-code-form" novalidate hidden>.*?</form>', page, re.S).group(0)
    # After the link's form: it shows once a link was asked for.
    assert page.index('id="signin-form"') < page.index(form) < page.index('id="signin-social"')
    assert 'id="signin-code"' in form and 'autocomplete="one-time-code"' in form and 'inputmode="numeric"' in form
    assert html.index('<script src="app.js">') < html.index('<script src="signin_code.js">')
    # app.js says when a link was asked for; signin_code.js sends the code with that address.
    assert 'new CustomEvent("signin:sent", { detail: { email } })' in (frontend / "app.js").read_text()
    script = (frontend / "signin_code.js").read_text()
    assert 'postJson("/api/auth/code", { email: askedFor, code })' in script
    assert 'window.addEventListener("signin:sent"' in script and "setUser(data.user)" in script


def test_session_cookie(client, outbox):
    _ask_link(client, "a@example.ch")
    r = client.post("/api/auth/verify", json={"token": _token(outbox[-1])})
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-session=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", f"Max-Age={90 * 24 * 3600}"):
        assert attribute in cookie
    assert "Domain" not in cookie
    # Only the hash is stored.
    value = client.cookies["__Host-session"]
    with sqlite3.connect(db.DB_PATH) as conn:
        assert [r[0] for r in conn.execute("SELECT token_hash FROM sessions")] == [auth.hash_token(value)]


def test_sign_out(client, outbox):
    _sign_in(client, outbox, "a@example.ch")
    r = client.post("/api/auth/sign_out")
    assert "__Host-session=" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
    assert client.get("/api/auth/me").json() == {"user": None}
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_sessions_slide_and_expire(client, outbox):
    _sign_in(client, outbox, "a@example.ch")
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ?",
                     (two_days_ago, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))

    r = client.get("/api/auth/me")
    assert "__Host-session=" in r.headers.get("set-cookie", "")  # sent again: 90 more days
    with sqlite3.connect(db.DB_PATH) as conn:
        expires = conn.execute("SELECT expires_at FROM sessions").fetchone()[0]
    assert datetime.fromisoformat(expires) > datetime.now(timezone.utc) + timedelta(days=89)
    assert "set-cookie" not in client.get("/api/auth/me").headers  # at most once a day

    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (two_days_ago,))
    assert client.get("/api/auth/me").json() == {"user": None}
    assert _code(client.get("/api/requests")) == "not_signed_in"


def test_returning_user_keeps_their_account(new_client, outbox):
    first = _sign_in(new_client(), outbox, "a@example.ch")
    other_browser = new_client()
    _ask_link(other_browser, "A@example.ch")
    r = other_browser.post("/api/auth/verify", json={"token": _token(outbox[-1])})
    assert r.json()["user"]["name"] == first["name"] and r.json()["user"]["profile_complete"]


def test_names_are_unique(new_client, outbox):
    assert _sign_in(new_client(), outbox, "a@example.ch", "Marie", "Müller")["name"] == "Marie M."
    assert _sign_in(new_client(), outbox, "b@example.ch", "Marie", "Meier")["name"] == "Marie M. 2"


def test_a_new_code_of_conduct_must_be_accepted(client, outbox, monkeypatch):
    _sign_in(client, outbox, "a@example.ch")
    monkeypatch.setattr(auth, "CONDUCT_VERSION", "2027-01")
    me = client.get("/api/auth/me").json()["user"]
    assert (me["profile_complete"], me["needs_conduct"], me["name"]) == (False, True, "Marie M.")
    assert _code(client.get("/api/requests")) == "profile_incomplete"
    r = client.post("/api/auth/profile", json={"adult": True, "accept_conduct": True, "first_name": "Other"})
    assert r.json()["user"]["name"] == "Marie M."  # the name stays
    assert client.get("/api/requests").status_code == 200


def test_email_in_every_language():
    for language in mailer.LANGUAGES:
        subject, text, html = mailer.sign_in_email(language, "https://x.ch/#/signin/abc", 15, "042917")
        assert subject == mailer.text(language, "email.signIn.subject")
        assert "https://x.ch/#/signin/abc" in text and 'href="https://x.ch/#/signin/abc"' in html
        assert "15" in text and "{" not in text
        assert "042917" in text and "042917" in html
    assert mailer.sign_in_email("xx", "l", 15, "042917")[0] == mailer.text("en", "email.signIn.subject")


def test_email_backend_chosen_by_environment(monkeypatch):
    monkeypatch.setattr(mailer, "_backend", None)
    monkeypatch.delenv("EMAIL_BACKEND", raising=False)
    assert isinstance(mailer.backend(), mailer.ConsoleBackend)
    monkeypatch.setattr(mailer, "_backend", None)
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    with pytest.raises(mailer.EmailError):
        mailer.backend()
    monkeypatch.setenv("EMAIL_FROM", "no-reply@example.ch")
    monkeypatch.setattr(mailer, "SesBackend", lambda sender, region: (sender, region))
    assert mailer.backend() == ("Ride and Rail <no-reply@example.ch>", "eu-north-1")


def test_no_ses_email_without_the_sites_address(client, outbox, monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert _code(_ask_link(client, "a@example.ch")) == "email_unavailable"
    assert outbox == []


def test_email_failure_is_a_503_and_leaves_no_usable_token(client, monkeypatch):
    class Failing:
        def send(self, *args):
            raise mailer.EmailError("down")

    monkeypatch.setattr(mailer, "_backend", Failing())
    r = _ask_link(client, "a@example.ch")
    assert (r.status_code, _code(r)) == (503, "email_unavailable")
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sign_in_links").fetchone()[0] == 0


# ---------------------------------------------------------------------
# Abuse limits
# ---------------------------------------------------------------------
def test_links_per_email_are_limited_silently(client, outbox):
    window, limit = auth.LINKS_PER_EMAIL[0]
    for _ in range(limit + 2):
        assert _ask_link(client, "a@example.ch").json() == {"ok": True}
    assert len(outbox) == limit


def test_links_per_ip_are_limited(client, outbox):
    window, limit = auth.LINKS_PER_IP[0]
    for i in range(limit):
        assert _ask_link(client, f"user{i}@example.ch").status_code == 200
    r = _ask_link(client, "one-more@example.ch")
    assert (r.status_code, _code(r)) == (429, "too_many_requests")
    assert len(outbox) == limit


# ---------------------------------------------------------------------
# Bans
# ---------------------------------------------------------------------
def test_ban_ends_sessions_and_blocks_sign_in(new_client, outbox):
    client = new_client()
    _sign_in(client, outbox, "bad@example.ch")
    _ask_link(new_client(), "bad@example.ch")  # a link asked for before the ban
    pending_token = _token(outbox[-1])

    assert db.set_banned("bad@example.ch", True)

    assert client.get("/api/auth/me").json() == {"user": None}
    assert _code(client.get("/api/requests")) == "not_signed_in"
    sent = len(outbox)
    assert _ask_link(new_client(), "bad@example.ch").json() == {"ok": True}  # same answer...
    assert len(outbox) == sent  # ...but no email
    r = new_client().post("/api/auth/verify", json={"token": pending_token})
    assert (r.status_code, _code(r)) == (403, "account_banned")

    db.set_banned("bad@example.ch", False)
    _ask_link(new_client(), "bad@example.ch")
    assert len(outbox) == sent + 1


def test_admin_script(isolated_db, capsys):
    import admin
    db.create_account("x@example.ch", "~x", "en")
    assert admin.main(["ban", "X@example.ch"]) == 0
    assert db.get_user_by_email("x@example.ch")["banned_at"]
    assert admin.main(["unban", "x@example.ch"]) == 0
    assert db.get_user_by_email("x@example.ch")["banned_at"] is None
    assert admin.main(["ban", "nobody@example.ch"]) == 1


# ---------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------
NEEDS_SIGN_IN = [
    ("POST", "/api/drives", {"origin": VEVEY, "destination": FRIBOURG, "departure_time": f"{TOMORROW}T08:00"}),
    ("GET", "/api/drives?mine=true", None),
    ("DELETE", "/api/drives/1", None),
    ("GET", "/api/ride_requests", None),
    ("POST", "/api/requests", {"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{IN_TWO_DAYS}T08:00"}),
    ("GET", "/api/requests", None),
    ("DELETE", "/api/requests/1", None),
    ("GET", "/api/notifications", None),
    ("POST", "/api/notifications/read", None),
    ("POST", "/api/notifications/1/action", {"action": "decline"}),
    ("PUT", "/api/notification_settings", {"notify_requests": False}),
    ("DELETE", "/api/account", None),
    ("POST", "/api/auth/profile", {"first_name": "A", "last_name": "B", "adult": True, "accept_conduct": True}),
]


@pytest.mark.parametrize("method, path, body", NEEDS_SIGN_IN)
def test_signed_in_only(client, method, path, body):
    r = client.request(method, path, json=body)
    assert (r.status_code, _code(r)) == (401, "not_signed_in")


def test_browsing_and_searching_stay_public(new_client, outbox):
    driver = new_client()
    _sign_in(driver, outbox, "d@example.ch", "Driver", "One")
    drive = _drive(driver)

    visitor = new_client()
    assert [d["id"] for d in visitor.get("/api/drives").json()] == [drive["id"]]
    # Who drives is for signed-in users (test_signed_out.py).
    assert visitor.get(f"/api/drives/{drive['id']}").json()["driver_name"] is None
    r = visitor.post("/api/match", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{TOMORROW}T18:00"})
    assert [m["id"] for m in r.json()["driver_matches"]] == [drive["id"]]
    with sqlite3.connect(db.DB_PATH) as conn:  # an anonymous search isn't kept
        assert conn.execute("SELECT COUNT(*) FROM ride_requests").fetchone()[0] == 0


def test_nobody_can_act_as_someone_else(new_client, outbox):
    marie, paul = new_client(), new_client()
    _sign_in(marie, outbox, "marie@example.ch", "Marie", "Muster")
    _sign_in(paul, outbox, "paul@example.ch", "Paul", "Peter")

    # A name in the body is ignored: the drive is the signed-in user's.
    r = marie.post("/api/drives", json={
        "driver_name": "Paul P.", "origin": VEVEY, "destination": FRIBOURG, "departure_time": f"{TOMORROW}T08:00",
    })
    assert r.json()["driver_name"] == "Marie M."
    pauls_drive = _drive(paul, day=IN_TWO_DAYS, at="07:00")
    assert [d["driver_name"] for d in marie.get("/api/drives?mine=true").json()] == ["Marie M."]
    assert [d["id"] for d in paul.get("/api/drives?mine=true").json()] == [pauls_drive["id"]]
    assert _code(marie.delete(f"/api/drives/{pauls_drive['id']}")) == "not_your_drive"

    pauls_request = _ride_request(paul)
    assert marie.get("/api/requests").json() == []
    assert _code(marie.delete(f"/api/requests/{pauls_request['id']}")) == "not_your_request"

    paul.post("/api/match", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{TOMORROW}T08:00"})
    assert marie.get("/api/ride_requests").json() == []
    assert [s["passenger_name"] for s in paul.get("/api/ride_requests").json()] == ["Paul P."]

    # Marie posts a drive that fits Paul's request: Paul is told, not Marie.
    _drive(marie, day=IN_TWO_DAYS, at="18:00")
    pauls_notification = paul.get("/api/notifications").json()["notifications"][0]
    assert marie.get("/api/notifications").json()["notifications"] == []
    r = marie.post(f"/api/notifications/{pauls_notification['id']}/action", json={"action": "view"})
    assert (r.status_code, _code(r)) == (404, "notification_not_found")
    assert paul.post(f"/api/notifications/{pauls_notification['id']}/action", json={"action": "view"}).status_code == 200


def test_cross_site_writes_are_refused(client, outbox):
    _sign_in(client, outbox, "a@example.ch")
    body = {"origin": VEVEY, "destination": FRIBOURG, "departure_time": f"{TOMORROW}T08:00"}
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
        r = client.post("/api/drives", json=body, headers=headers)
        assert (r.status_code, _code(r)) == (403, "cross_site_request")
        assert _code(client.delete("/api/account", headers=headers)) == "cross_site_request"
    assert client.post("/api/drives", json=body, headers={"Origin": SITE}).status_code == 200
    assert client.post("/api/drives", json=body, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    # Reading isn't affected.
    assert client.get("/api/drives", headers={"Origin": "https://evil.example"}).status_code == 200


# ---------------------------------------------------------------------
# Viewing the demo as a demo driver
# ---------------------------------------------------------------------
def test_demo_driver_view_is_read_only(new_client, outbox):
    real = new_client()
    _sign_in(real, outbox, "real@example.ch")
    sent = len(outbox)

    demo = new_client()
    assert "Jean Dupont" in demo.get("/api/auth/demo_drivers").json()
    r = demo.post("/api/auth/demo", json={"driver_name": "Jean Dupont"})
    user = r.json()["user"]
    assert (user["name"], user["is_demo"], user["email"], user["profile_complete"]) == ("Jean Dupont", True, None, True)
    assert f"Max-Age={int(auth.DEMO_SESSION_TTL.total_seconds())}" in r.headers["set-cookie"]

    assert demo.get("/api/drives?mine=true").status_code == 200
    assert demo.get("/api/notifications").status_code == 200
    assert demo.get("/api/requests").json() == []
    for method, path, body in NEEDS_SIGN_IN:
        if method != "GET" and path != "/api/notifications/read":
            assert _code(demo.request(method, path, json=body)) == "demo_read_only", path
    # Searching works, but isn't kept.
    demo.post("/api/match", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{TOMORROW}T08:00"})
    assert demo.get("/api/ride_requests").json() == []

    assert _code(new_client().post("/api/auth/demo", json={"driver_name": "Marie M."})) == "demo_driver_not_found"
    assert db.get_user(db.get_or_create_user("Jean Dupont"))["email"] is None
    assert len(outbox) == sent  # demo drivers never get email


def test_demo_driver_doesnt_see_real_passengers_requests(new_client, outbox, monkeypatch):
    import notify
    passenger = new_client()
    _sign_in(passenger, outbox, "p@example.ch")
    # Make Jean Dupont the driver told about the request.
    monkeypatch.setattr(notify, "notify_drivers", lambda request, **kw: [
        db.create_notification("Jean Dupont", "request_for_driver", request["id"])["recipient_name"]
    ])
    _ride_request(passenger)
    db.get_or_create_user("Jean Dupont", is_demo=True)

    demo = new_client()
    demo.post("/api/auth/demo", json={"driver_name": "Jean Dupont"})
    assert demo.get("/api/notifications").json()["notifications"] == []
    assert len(notify.inbox("Jean Dupont")["notifications"]) == 1


# ---------------------------------------------------------------------
# Deleting an account
# ---------------------------------------------------------------------
def test_account_deletion(new_client, outbox):
    driver, passenger, other = new_client(), new_client(), new_client()
    _sign_in(driver, outbox, "driver@example.ch", "Dora", "Driver")
    _sign_in(passenger, outbox, "passenger@example.ch", "Pia", "Passenger")
    _sign_in(other, outbox, "other@example.ch", "Otto", "Other")

    request = _ride_request(passenger)
    _drive(driver, day=IN_TWO_DAYS, at="18:00")  # fits the request: Pia is told
    driver.post("/api/match", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{TOMORROW}T08:00"})
    _ride_request(driver, day=IN_TWO_DAYS, at="07:00")
    # Otto was told about Dora's request.
    dora_request = driver.get("/api/requests").json()[0]
    db.create_notification("Otto O.", "request_for_driver", dora_request["id"])
    driver_id = db.get_user_by_email("driver@example.ch")["id"]

    r = driver.delete("/api/account")
    assert r.json() == {"deleted": True}
    assert "Max-Age=0" in r.headers["set-cookie"]

    assert db.get_user_by_email("driver@example.ch") is None
    with sqlite3.connect(db.DB_PATH) as conn:
        for table, column in (("drives", "driver_id"), ("ride_requests", "passenger_id"),
                              ("open_requests", "passenger_id"), ("notifications", "recipient_id"),
                              ("sessions", "user_id"), ("route_mutes", "user_id")):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (driver_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sign_in_links WHERE email = 'driver@example.ch'").fetchone()[0] == 0
    # Pia hears the drive is cancelled; Otto's notification about Dora's request is gone.
    kinds = [n["kind"] for n in passenger.get("/api/notifications").json()["notifications"]]
    assert kinds == ["drive_cancelled", "drive_for_passenger"]
    assert other.get("/api/notifications").json()["notifications"] == []
    assert passenger.get("/api/requests").json()[0]["id"] == request["id"]
    # The old cookie is useless.
    assert driver.get("/api/auth/me").json() == {"user": None}
    assert _code(driver.get("/api/requests")) == "not_signed_in"


def test_an_unfinished_account_can_be_deleted(client, outbox):
    _ask_link(client, "a@example.ch")
    client.post("/api/auth/verify", json={"token": _token(outbox[-1])})
    assert client.delete("/api/account").json() == {"deleted": True}
    assert db.get_user_by_email("a@example.ch") is None


# ---------------------------------------------------------------------
# Existing databases
# ---------------------------------------------------------------------
def test_schema_migrates_an_existing_database(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:  # users and drives as before sign-in
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                                notify_requests INTEGER NOT NULL DEFAULT 1, backoff_reset_at TEXT);
            INSERT INTO users (name) VALUES ('Jean Dupont'), ('Typed Name');
            CREATE TABLE drives (
                id INTEGER PRIMARY KEY AUTOINCREMENT, driver_id INTEGER NOT NULL, driver_name TEXT NOT NULL,
                origin_name TEXT NOT NULL, origin_lat REAL NOT NULL, origin_lon REAL NOT NULL,
                destination_name TEXT NOT NULL, destination_lat REAL NOT NULL, destination_lon REAL NOT NULL,
                departure_time TEXT NOT NULL, seats_available INTEGER NOT NULL DEFAULT 1, route_geometry TEXT,
                route_distance_m REAL, route_duration_s REAL, route_source TEXT, created_at TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO drives (driver_id, driver_name, origin_name, origin_lat, origin_lon, destination_name,
                                destination_lat, destination_lon, departure_time, created_at, is_demo)
            VALUES (1, 'Jean Dupont', 'A', 0, 0, 'B', 0, 0, '2026-11-20T07:15', '2026-11-18', 1);
        """)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    db.init_db()  # and again: nothing left to do

    jean, typed = db.get_user(1), db.get_user(2)
    assert (jean["is_demo"], typed["is_demo"]) == (1, 0)
    assert typed["email"] is None and typed["banned_at"] is None
    db.create_account("new@example.ch", "~new", "de")
    with pytest.raises(sqlite3.IntegrityError):  # one account per email
        db.create_account("new@example.ch", "~other", "de")
