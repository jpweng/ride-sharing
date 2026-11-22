"""
The contact form (contact.py, routes/contact.py, frontend/contact.html,
docs/contact.md), through HTTP like the browser: switched off until
CONTACT_EMAIL is set, the email the team gets and its headers (Reply-To the
visitor), what is refused, the limits, and that nothing a visitor types can
add a header. Emails are kept in memory (the console backend), no network.
"""
import json
import logging
import re
from pathlib import Path

import pytest
from botocore.stub import Stubber

import contact
import mailer
from test_auth import SITE, _sign_in, client, new_client, outbox  # noqa: F401 (fixtures)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
LANGS = ["en", "de", "fr", "it"]
TEAM = "team@example.ch"
MESSAGE = {
    "name": "Marie Muster",
    "email": "Marie@Example.ch",
    "message": "Bonjour,\n\nje n'arrive pas à me connecter.\nMerci !",
    "language": "fr",
}


def _code(response):
    return response.json()["detail"]["code"]


@pytest.fixture(autouse=True)
def no_messages_counted():
    contact._sent.clear()
    yield
    contact._sent.clear()


@pytest.fixture
def contact_on(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", TEAM)


# --- switched off until there is somewhere to send messages -------------------

def test_off_until_the_address_is_set(client, outbox, monkeypatch):
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    assert client.get("/api/contact").json() == {"enabled": False}
    r = client.post("/api/contact", json=MESSAGE)
    assert (r.status_code, _code(r)) == (503, "contact_unavailable")
    assert outbox == []


def test_a_mistyped_address_leaves_it_off(client, outbox, monkeypatch, caplog):
    monkeypatch.setenv("CONTACT_EMAIL", "not an address")
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert client.get("/api/contact").json() == {"enabled": False}
    assert "CONTACT_EMAIL" in caplog.text
    assert client.post("/api/contact", json=MESSAGE).status_code == 503
    assert outbox == []


# --- the email the team gets ------------------------------------------------------

def test_the_message_is_emailed_to_the_team(client, outbox, contact_on):
    assert client.get("/api/contact").json() == {"enabled": True}
    assert client.post("/api/contact", json=MESSAGE).json() == {"ok": True}
    [email] = outbox
    assert email["to"] == TEAM
    # Answering the email answers the visitor.
    assert email["reply_to"] == "marie@example.ch"
    assert email["subject"] == "[Ride and Rail contact] Marie Muster"
    assert MESSAGE["message"] in email["text"]
    assert "From:     Marie Muster <marie@example.ch>" in email["text"]
    assert "Account:  not signed in" in email["text"]
    assert "Language: fr" in email["text"]


def test_the_email_says_which_account_wrote(client, outbox, contact_on):
    user = _sign_in(client, outbox, "marie@example.ch")
    client.post("/api/contact", json={**MESSAGE, "email": "other@example.ch"})
    text = outbox[-1]["text"]
    assert re.search(r"Account:  signed in as marie@example\.ch \(user \d+\)", text), text
    # The answer goes where the visitor asked for it.
    assert outbox[-1]["reply_to"] == "other@example.ch"
    assert user["email"] == "marie@example.ch"


def test_the_message_is_escaped_in_the_html(client, outbox, contact_on):
    client.post("/api/contact", json={**MESSAGE, "name": "<b>Eve</b>", "message": "<script>alert(1)</script>"})
    html = outbox[-1]["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>Eve</b>" not in html


def test_ses_gets_the_reply_to(monkeypatch):
    # The real API's parameters, checked by botocore against Amazon SES's own description.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    backend = mailer.SesBackend("Ride and Rail <no-reply@example.ch>", "eu-north-1")
    content = {"Simple": {
        "Subject": {"Data": "s", "Charset": "UTF-8"},
        "Body": {"Text": {"Data": "t", "Charset": "UTF-8"}, "Html": {"Data": "<p>h</p>", "Charset": "UTF-8"}},
    }}
    common = {"FromEmailAddress": "Ride and Rail <no-reply@example.ch>", "Destination": {"ToAddresses": [TEAM]},
              "Content": content}
    with Stubber(backend.client) as ses:
        ses.add_response("send_email", {"MessageId": "1"}, {**common, "ReplyToAddresses": ["marie@example.ch"]})
        backend.send(TEAM, "s", "t", "<p>h</p>", reply_to="marie@example.ch")
        # Every other email: no Reply-To.
        ses.add_response("send_email", {"MessageId": "2"}, common)
        backend.send(TEAM, "s", "t", "<p>h</p>")
        ses.assert_no_pending_responses()


# --- what is refused ----------------------------------------------------------------

@pytest.mark.parametrize("missing", ["name", "email", "message"])
def test_name_address_and_message_are_all_needed(client, outbox, contact_on, missing):
    r = client.post("/api/contact", json={**MESSAGE, missing: "  "})
    assert (r.status_code, _code(r)) == (400, "contact_incomplete")
    assert outbox == []


def test_the_address_must_be_one(client, outbox, contact_on):
    for email in ("marie", "marie@example", "marie@example.ch\nBcc: all@example.ch", "a b@example.ch"):
        r = client.post("/api/contact", json={**MESSAGE, "email": email})
        assert (r.status_code, _code(r)) == (400, "invalid_email"), email
    assert outbox == []


def test_nothing_typed_can_add_a_header(client, outbox, contact_on):
    client.post("/api/contact", json={**MESSAGE, "name": "Eve\r\nBcc: all@example.ch\r\n\tX"})
    [email] = outbox
    assert email["subject"] == "[Ride and Rail contact] Eve Bcc: all@example.ch X"
    assert not re.search(r"[\r\n]", email["subject"] + email["reply_to"] + email["to"])


def test_a_long_message_is_refused(client, outbox, contact_on):
    r = client.post("/api/contact", json={**MESSAGE, "message": "x" * (contact.MAX_MESSAGE_CHARS + 1)})
    assert r.status_code == 422
    assert client.post("/api/contact", json={**MESSAGE, "message": "x" * contact.MAX_MESSAGE_CHARS}).status_code == 200
    assert len(outbox) == 1


def test_the_hidden_field_drops_a_robots_message(client, outbox, contact_on):
    # Answered like any other, so the robot learns nothing.
    assert client.post("/api/contact", json={**MESSAGE, "website": "https://spam.example"}).json() == {"ok": True}
    assert outbox == []


def test_cross_site_messages_are_refused(client, outbox, contact_on):
    r = client.post("/api/contact", json=MESSAGE, headers={"Origin": "https://evil.example"})
    assert (r.status_code, _code(r)) == (403, "cross_site_request")
    assert client.post("/api/contact", json=MESSAGE, headers={"Origin": SITE}).status_code == 200
    assert len(outbox) == 1


# --- limits -------------------------------------------------------------------------

def test_a_few_messages_per_address(client, outbox, contact_on):
    (window, limit), _ = contact.PER_IP
    for _ in range(limit):
        assert client.post("/api/contact", json=MESSAGE).status_code == 200
    r = client.post("/api/contact", json=MESSAGE)
    assert (r.status_code, _code(r)) == (429, "too_many_requests")
    assert len(outbox) == limit
    # Once the window has passed, the next one goes through.
    contact._sent = type(contact._sent)((at - window, ip) for at, ip in contact._sent)
    assert client.post("/api/contact", json=MESSAGE).status_code == 200


def test_a_limit_for_all_visitors_together(client, outbox, contact_on, monkeypatch):
    monkeypatch.setattr(contact, "PER_DAY", 2)
    for ip in ("203.0.113.1", "203.0.113.2"):
        contact.send("Marie", "marie@example.ch", "Hello", ip=ip)
    with pytest.raises(Exception) as refused:
        contact.send("Marie", "marie@example.ch", "Hello", ip="203.0.113.3")
    assert refused.value.detail["code"] == "too_many_requests"
    assert len(outbox) == 2


def test_a_message_that_couldnt_be_sent_isnt_counted(client, contact_on, monkeypatch):
    class Failing:
        def send(self, *args, **kwargs):
            raise mailer.EmailError("the provider is down")

    monkeypatch.setattr(mailer, "_backend", Failing())
    for _ in range(contact.PER_IP[0][1] + 1):
        r = client.post("/api/contact", json=MESSAGE)
        assert (r.status_code, _code(r)) == (503, "email_unavailable")
    assert len(contact._sent) == 0


def test_the_log_keeps_neither_the_sender_nor_the_message(client, outbox, contact_on, caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        client.post("/api/contact", json={**MESSAGE, "message": "A secret told to the team"})
    [record] = [r for r in caplog.records if r.getMessage().startswith("Contact form")]
    assert "secret" not in record.getMessage() and "marie" not in record.getMessage().lower()


# --- the page ---------------------------------------------------------------------

def test_the_page_has_the_form():
    html = (FRONTEND / "contact.html").read_text()
    for field in ("contact-name", "contact-email", "contact-message", "contact-website"):
        assert f'id="{field}"' in html, field
    assert f'maxlength="{contact.MAX_MESSAGE_CHARS}"' in html
    assert f'maxlength="{contact.MAX_NAME_CHARS}"' in html
    assert '<script src="contact.js"></script>' in html
    # The field for robots is out of sight, of the keyboard's way and of screen readers'.
    assert re.search(r'<div class="contact-extra" aria-hidden="true">.*?tabindex="-1"', html, re.S)
    assert ".contact-extra" in (FRONTEND / "styles" / "contact.css").read_text()


@pytest.mark.parametrize("lang", LANGS)
def test_the_site_links_to_the_page(lang):
    banner = json.loads((FRONTEND / "i18n" / lang / "banner.json").read_text())
    assert 'href="contact.html"' in banner["banner.app"]
    # The privacy notice and the code of conduct say how to reach the team.
    for page in ("privacy.html", "conduct.html"):
        article = re.search(rf'<article[^>]*lang="{lang}".*?</article>', (FRONTEND / page).read_text(), re.S).group(0)
        assert 'href="contact.html"' in article, page


def test_the_banner_of_the_app_links_to_the_page():
    assert '<a href="contact.html">Contact</a>' in (FRONTEND / "index.html").read_text()
