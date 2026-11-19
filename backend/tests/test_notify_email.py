"""
Notifications by email (notify_email.py): what is sent to whom and when,
what is held or skipped, failures, and turning the emails off from the
inbox or from an email's link. Emails are kept in memory (the console
backend), isolated database, no network.
"""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import database as db
import mailer
import main
import notify
import notify_email
import translations
from routes import inbox as inbox_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
NOON = datetime.fromisoformat(f"{date.today().isoformat()}T12:00")
NIGHT = datetime.fromisoformat(f"{date.today().isoformat()}T23:30")
MORNING = datetime.fromisoformat(f"{TOMORROW}T07:05")

VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
FRIBOURG = {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619}
DRIVE = {"id": 1, "driver_name": "Jean D.", "origin_name": "Vevey", "destination_name": "Fribourg",
         "departure_time": f"{TOMORROW}T08:00"}
BOOKING = {"booking_id": 1, "passenger_name": "Marie M.", "seats": 1, "pickup_time": f"{TOMORROW}T08:00", "drive": DRIVE}


@pytest.fixture
def outbox(monkeypatch, isolated_db):
    backend = mailer.ConsoleBackend()
    monkeypatch.setattr(mailer, "_backend", backend)
    monkeypatch.delenv("EMAIL_BACKEND", raising=False)
    monkeypatch.delenv("EMAIL_NOTIFICATIONS", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://rideandrail.example")
    return backend.outbox


@pytest.fixture
def account(isolated_db):
    """name, email, language -> an account with its profile done."""
    def make(name, email, language="en"):
        user = db.create_account(email, f"~{name}", language)
        first, _, last = name.partition(" ")
        return db.set_profile(user["id"], auth.CONDUCT_VERSION, name, first, last)
    return make


def _request_notification(driver_name, passenger="Marie M."):
    request = db.create_open_request(passenger, VEVEY, FRIBOURG, f"{TOMORROW}T18:30", 5, 5, 120)
    return notify._send(driver_name, "request_for_driver", request["id"], details={"area": {"name": "Vevey"}, "ends": ["origin"]})


def _state(n):
    return db.get_notification(n["id"])["email_state"]


# --- What is sent ----------------------------------------------------------------

def test_a_request_is_emailed_to_the_driver_in_their_language(outbox, account):
    account("Zoe Z.", "zoe@example.ch", "de")
    n = _request_notification("Zoe Z.")
    assert outbox == []  # nothing leaves when the notification is made

    assert notify_email.send_pending(NOON) == {"sent": 1, "skipped": 0, "failed": 0, "held": 0}
    [email] = outbox
    assert email["to"] == "zoe@example.ch"
    assert email["subject"] == "Marie M. sucht eine Fahrt, die Sie anbieten könnten"
    assert "Vevey → Fribourg" in email["text"] and "18:30" in email["text"]
    assert "https://rideandrail.example/?lang=de#/inbox" in email["text"]
    off = re.search(r"https://rideandrail\.example/unsubscribe/([\w-]+)", email["text"])
    assert off and off.group(0) in email["html"]
    assert _state(n) == "sent"
    # Once.
    assert notify_email.send_pending(NOON)["sent"] == 0
    assert len(outbox) == 1


def test_booking_news_is_emailed_to_the_other_one(outbox, account):
    account("Jean D.", "jean@example.ch", "fr")
    account("Marie M.", "marie@example.ch", "it")
    notify.send("Jean D.", "booking_requested", BOOKING)
    notify.send("Marie M.", "booking_accepted", BOOKING)
    notify_email.send_pending(NOON)
    assert [(e["to"], e["subject"]) for e in outbox] == [
        ("jean@example.ch", "Marie M. demande une place sur votre trajet"),
        ("marie@example.ch", "Jean D. ha accettato la sua richiesta di un posto"),
    ]
    assert "Vevey → Fribourg" in outbox[0]["text"] and "08:00" in outbox[0]["text"]


KINDS = [
    ("request_for_driver", {}), ("drive_for_passenger", {}), ("drive_cancelled", {"drive": DRIVE}),
    ("booking_requested", BOOKING), ("booking_accepted", BOOKING), ("booking_declined", BOOKING),
    ("booking_cancelled", {**BOOKING, "by": "driver", "was": "accepted"}),
    ("booking_cancelled", {**BOOKING, "by": "passenger", "was": "accepted"}),
    ("booking_drive_cancelled", {**BOOKING, "was": "accepted"}),
    ("request_accepted", BOOKING),
    ("rate_ride", {"booking_id": 1, "rated_role": "driver", "rated_name": "Jean D.", "drive": DRIVE}),
    ("rate_ride", {"booking_id": 1, "rated_role": "passenger", "rated_name": "Marie M.", "drive": DRIVE}),
    ("a_kind_added_later", {}),
]


@pytest.mark.parametrize("language", mailer.LANGUAGES)
def test_every_kind_has_its_sentence_in_every_language(outbox, account, language):
    user = account("Zoe Z.", "zoe@example.ch", language)
    request = db.create_open_request("Marie M.", VEVEY, FRIBOURG, f"{TOMORROW}T18:30", 5, 5, 120)
    subjects = set()
    for kind, details in KINDS:
        n = notify._send("Zoe Z.", kind, request["id"], details=details)
        subject, plain, html = notify_email.compose(db.get_notification(n["id"]), user)
        assert subject and "{" not in subject and "{" not in plain, kind
        assert "<script" not in html
        subjects.add(subject)
    assert len(subjects) == len(KINDS)


def test_the_texts_exist():
    source = (Path(notify_email.__file__)).read_text()
    keys = {f"email.notify.{key}" for key in re.findall(r'\bt\("(\w+)"', source)}
    assert len(keys) >= 12
    assert keys - set(translations.dictionary("en")) == set()


def test_names_are_escaped_in_the_html(outbox, account):
    user = account("Zoe Z.", "zoe@example.ch")
    n = notify.send("Zoe Z.", "booking_requested", {**BOOKING, "passenger_name": "<b>Mallory</b>"})
    _, _, html = notify_email.compose(db.get_notification(n["id"]), user)
    assert "<b>Mallory" not in html and "&lt;b&gt;Mallory" in html


# --- When ------------------------------------------------------------------------

def test_what_can_wait_waits_for_the_morning(outbox, account):
    account("Zoe Z.", "zoe@example.ch")
    n = _request_notification("Zoe Z.")
    assert notify_email.send_pending(NIGHT) == {"sent": 0, "skipped": 0, "failed": 0, "held": 1}
    assert outbox == [] and _state(n) is None
    assert notify_email.send_pending(datetime.fromisoformat(f"{TOMORROW}T06:59"))["held"] == 1
    assert notify_email.send_pending(MORNING)["sent"] == 1
    assert len(outbox) == 1


def test_booking_news_goes_out_at_night_too(outbox, account):
    account("Marie M.", "marie@example.ch")
    notify.send("Marie M.", "booking_drive_cancelled", {**BOOKING, "was": "accepted"})
    notify.send("Marie M.", "request_accepted", BOOKING)
    assert notify_email.send_pending(NIGHT)["sent"] == 2
    assert [e["subject"] for e in outbox] == [
        "Jean D. cancelled the drive you were going to join", "Jean D. will drive you",
    ]


def test_the_question_how_a_ride_went_waits_for_the_morning(outbox, account):
    account("Marie M.", "marie@example.ch")
    notify.send("Marie M.", "rate_ride", {"booking_id": 1, "rated_role": "driver", "rated_name": "Jean D.", "drive": DRIVE})
    assert notify_email.send_pending(NIGHT)["held"] == 1
    assert notify_email.send_pending(MORNING)["sent"] == 1
    assert outbox[0]["subject"] == "How was your ride with Jean D.?"


# --- Who gets none ---------------------------------------------------------------

def test_no_email_for_those_who_shouldnt_get_one(outbox, account, user_named):
    off = account("Off O.", "off@example.ch")
    notify_email.set_enabled(off["id"], False)
    account("Banned B.", "banned@example.ch")
    db.set_banned("banned@example.ch", True)
    account("Read R.", "read@example.ch")
    user_named("Noemail N.")  # a name without an account
    account("Old O.", "old@example.ch")

    made = {name: notify.send(name, "booking_accepted", BOOKING) for name in
            ("Off O.", "Banned B.", "Read R.", "Noemail N.", "Old O.")}
    db.mark_notifications_read("Read R.")  # seen in the app before the email left
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE notifications SET created_at = ? WHERE id = ?", (long_ago, made["Old O."]["id"]))

    assert notify_email.send_pending(NOON) == {"sent": 0, "skipped": 5, "failed": 0, "held": 0}
    assert outbox == []
    assert {_state(n) for n in made.values()} == {"skipped"}


def test_switched_off_for_everyone(outbox, account, monkeypatch):
    account("Zoe Z.", "zoe@example.ch")
    n = notify.send("Zoe Z.", "booking_accepted", BOOKING)
    monkeypatch.setenv("EMAIL_NOTIFICATIONS", "off")
    assert notify_email.send_pending(NOON)["skipped"] == 1
    assert outbox == [] and _state(n) == "skipped"
    # And not sent after the fact when it's switched back on.
    monkeypatch.setenv("EMAIL_NOTIFICATIONS", "on")
    assert notify_email.send_pending(NOON) == {"sent": 0, "skipped": 0, "failed": 0, "held": 0}


@pytest.mark.parametrize("missing", ["EMAIL_FROM", "PUBLIC_BASE_URL"])
def test_no_emails_until_sending_is_set_up(outbox, account, monkeypatch, missing):
    monkeypatch.setenv("EMAIL_BACKEND", "ses")
    monkeypatch.setenv("EMAIL_FROM", "no-reply@rideandrail.example")
    monkeypatch.delenv(missing)
    account("Zoe Z.", "zoe@example.ch")
    notify.send("Zoe Z.", "booking_accepted", BOOKING)
    assert notify_email.send_pending(NOON)["skipped"] == 1
    assert outbox == []


def test_a_failing_email_is_tried_three_times_and_holds_nothing_up(outbox, account, monkeypatch):
    account("Zoe Z.", "zoe@example.ch")
    account("Marie M.", "marie@example.ch")
    bad = notify.send("Zoe Z.", "booking_accepted", BOOKING)
    good = notify.send("Marie M.", "booking_accepted", BOOKING)

    real_send = mailer.backend().send

    def send(to, subject, text, html):
        if to == "zoe@example.ch":
            raise mailer.EmailError("the provider said no")
        real_send(to, subject, text, html)
    monkeypatch.setattr(mailer.backend(), "send", send)

    assert notify_email.send_pending(NOON) == {"sent": 1, "skipped": 0, "failed": 1, "held": 0}
    assert _state(good) == "sent" and _state(bad) is None
    notify_email.send_pending(NOON)
    assert notify_email.send_pending(NOON)["failed"] == 1
    assert _state(bad) == "failed"
    assert notify_email.send_pending(NOON) == {"sent": 0, "skipped": 0, "failed": 0, "held": 0}


# --- Turning them off --------------------------------------------------------------

def test_the_inbox_has_the_switch(outbox, account):
    zoe = {**account("Zoe Z.", "zoe@example.ch"), "is_demo": False}
    assert inbox_routes.get_notifications(zoe)["notify_email"] is True
    changed = inbox_routes.update_notification_settings(inbox_routes.NotificationSettings(notify_email=False), zoe)
    assert changed["notify_email"] is False and changed["notify_requests"] is True
    notify.send("Zoe Z.", "booking_accepted", BOOKING)
    assert notify_email.send_pending(NOON)["skipped"] == 1
    # The other setting leaves it alone.
    again = inbox_routes.update_notification_settings(inbox_routes.NotificationSettings(notify_requests=False), zoe)
    assert (again["notify_email"], again["notify_requests"]) == (False, False)


def test_the_emails_link_turns_them_off_after_asking(outbox, account):
    zoe = account("Zoe Z.", "zoe@example.ch", "fr")
    notify.send("Zoe Z.", "booking_accepted", BOOKING)
    notify_email.send_pending(NOON)
    path = re.search(r"/unsubscribe/[\w-]+", outbox[0]["text"]).group(0)
    client = TestClient(main.app, base_url="https://testserver")

    # Opening the link (as a mail scanner would) only asks.
    page = client.get(path)
    assert page.status_code == 200 and 'method="post"' in page.text
    assert "Ne plus recevoir ces e-mails" in page.text
    assert notify_email.enabled_for(zoe["id"]) is True

    done = client.post(path)
    assert done.status_code == 200 and "E-mails désactivés" in done.text
    assert notify_email.enabled_for(zoe["id"]) is False
    notify.send("Zoe Z.", "booking_declined", BOOKING)
    assert notify_email.send_pending(NOON)["skipped"] == 1
    assert len(outbox) == 1


def test_an_unknown_link_says_the_same_and_changes_nothing(outbox, account):
    zoe = account("Zoe Z.", "zoe@example.ch")
    client = TestClient(main.app, base_url="https://testserver")
    assert client.get("/unsubscribe/not-a-token").status_code == 200
    assert "Emails turned off" in client.post("/unsubscribe/not-a-token").text
    assert notify_email.enabled_for(zoe["id"]) is True
