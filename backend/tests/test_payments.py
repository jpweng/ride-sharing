"""
Paying for a seat (payments.py, routes/payments.py, docs/payments.md):
switched off without Stripe's keys; with them, a payment due from the
driver's acceptance, the Checkout page, Stripe's webhook, refunds when a
booking ends, and the bookings cancelled when they aren't paid in time.
Stripe itself is a fake (fake_stripe.py): no network. Isolated temp DB and
haversine routing, the route functions called directly as a given user.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import bookings
import database as db
import main
import maintenance
import matching
import notify
import notify_email
import payments
import pricing
import stripe_gateway
import translations
from fake_stripe import NOW, at_noon, stripe  # noqa: F401 (fixtures)
from routes import auth as auth_routes
from routes import bookings as booking_routes
from routes import drives as drive_routes
from routes import ride_requests as request_routes
from routes.match import MatchRequest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
LANGS = ["en", "de", "fr", "it"]

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
BASE = "https://rideandrail.test"

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}


@pytest.fixture
def users(user_named, force_haversine):
    """Jean drives; Marie and Paul ride."""
    return {name.split()[0].lower(): user_named(name) for name in ("Jean D.", "Marie M.", "Paul P.")}


def _drive(driver, seats=3, at="08:00", day=TOMORROW, **fields):
    return drive_routes.post_drive(drive_routes.DriveCreate(
        origin=LAUSANNE, destination=GENEVE, departure_time=f"{day}T{at}", seats_available=seats, **fields,
    ), driver)


def _booked(users, passenger="marie", **fields):
    """A drive of Jean's with the passenger's booking on it, accepted."""
    drive = fields.pop("drive", None) or _drive(users["jean"])
    asked = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(**fields), users[passenger])
    return booking_routes.accept_booking(asked["id"], users["jean"])


def _pay(stripe, booking, user):
    """The passenger opens the page, pays, and Stripe tells the site."""
    payments.checkout(booking["id"], user, BASE)
    payments._apply_checkout(stripe.pay(stripe.last()))


def _mine(user, side="as_passenger"):
    return booking_routes.get_bookings(user)[side]


def _payment(booking):
    return payments.for_booking(booking["id"])


def _refused(call, *args, **kwargs):
    with pytest.raises(HTTPException) as exc:
        call(*args, **kwargs)
    return exc.value.status_code, exc.value.detail["code"]


def _inbox(user):
    return [(n["kind"], n["details"]) for n in reversed(notify.inbox(user["name"])["notifications"])]


def _kinds(user):
    return [kind for kind, _ in _inbox(user)]


# --- Switched off -----------------------------------------------------------

def test_off_without_the_keys(users):
    assert payments.enabled() is False
    assert TestClient(main.app).get("/api/payments/config").json() == {"enabled": False, "fee": None}
    booking = _booked(users)
    # Bookings work as before: nothing to pay.
    assert _payment(booking) is None
    assert _mine(users["marie"])[0]["payment"] is None
    assert _refused(payments.checkout, booking["id"], users["marie"], BASE) == (503, "payments_off")
    assert TestClient(main.app).post("/api/payments/webhook", content=b"{}").status_code == 503
    # And the hourly run has nothing to do.
    assert payments.run(NOW) == {
        "unpaid_cancelled": 0, "payments_ended": 0, "prices_settled": 0, "refunds_sent": 0, "payments_deleted": 0,
    }


@pytest.mark.parametrize("only", payments.SETTINGS)
def test_half_a_setup_stays_off(only, monkeypatch):
    monkeypatch.setenv(only, "x")
    assert payments.enabled() is False


def test_bookings_accepted_before_the_switch_are_never_asked_to_pay(users, stripe, monkeypatch):
    for name in payments.SETTINGS:
        monkeypatch.delenv(name)
    booking = _booked(users)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")

    assert _mine(users["marie"])[0]["payment"] is None
    assert _refused(payments.checkout, booking["id"], users["marie"], BASE) == (400, "payment_not_due")
    # Long after its time to pay would have passed: still booked.
    assert payments.cancel_unpaid(NOW + timedelta(days=5)) == 0
    assert _mine(users["marie"])[0]["status"] == "accepted"


# --- A payment is due from the driver's acceptance ---------------------------

def test_an_accepted_booking_is_to_pay(users, stripe):
    drive = _drive(users["jean"])
    asked = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(), users["marie"])
    # A request has nothing to pay yet.
    assert _mine(users["marie"])[0]["payment"] is None
    assert _refused(payments.checkout, asked["id"], users["marie"], BASE) == (400, "payment_not_due")

    booking = booking_routes.accept_booking(asked["id"], users["jean"])

    mine = _mine(users["marie"])[0]
    assert mine["payment"] == {
        "status": "due", "amount_chf": mine["total_chf"], "fee_chf": 0.0, "refunded_chf": 0.0,
        # The pickup, tomorrow at 08:00, comes before 24 hours from now.
        "pay_by": f"{TOMORROW}T08:00",
        "free_cancel_until": None, "refund_if_cancelled_chf": None, "on_hold": False, "can_dispute": False,
    }
    # The driver sees it too.
    assert _mine(users["jean"], "as_driver")[0]["payment"]["status"] == "due"
    assert _payment(booking)["driver_id"] == users["jean"]["id"]


def test_a_day_to_pay_when_the_ride_is_further_away(users, stripe):
    in_a_week = (date.today() + timedelta(days=7)).isoformat()
    _booked(users, drive=_drive(users["jean"], day=in_a_week))
    assert _mine(users["marie"])[0]["payment"]["pay_by"] == (NOW + payments.PAY_WITHIN).isoformat(timespec="minutes")


def test_a_ride_request_accepted_directly_is_to_pay(users, stripe):
    asked = request_routes.create_ride_request(MatchRequest(
        origin=LAUSANNE, destination=GENEVE, requested_time=f"{TOMORROW}T08:00",
    ), users["marie"])["request"]
    booking = request_routes.accept_ride_request(asked["id"], request_routes.RequestAccept(), users["jean"])
    assert booking["status"] == "accepted"
    assert _mine(users["marie"])[0]["payment"]["status"] == "due"


# --- Paying -----------------------------------------------------------------

def test_the_checkout_page(users, stripe):
    booking = _booked(users, seats=2)
    url = payments.checkout(booking["id"], users["marie"], BASE)

    assert url == "https://checkout.test/cs_1"
    asked = stripe.sessions["cs_1"]["asked"]
    # The price per seat as it is now, times the seats, in centimes.
    assert asked["amount_cts"] == round(booking["total_chf"] * 100) > 0
    assert booking["total_chf"] == round(booking["price_chf"] * 2, 2)
    when = datetime.fromisoformat(f"{TOMORROW}T08:00").strftime("%d.%m.%Y %H:%M")
    assert asked["description"] == f"Ride Lausanne → Genève, {when} (2 seats)"
    assert asked["success_url"] == f"{BASE}/?paid={booking['id']}#/ride"
    assert asked["cancel_url"] == f"{BASE}/?unpaid={booking['id']}#/ride"
    assert asked["metadata"] == {
        "payment_id": str(_payment(booking)["id"]), "booking_id": str(booking["id"]), "fee_cts": "0",
    }
    assert asked["fee_cts"] == 0  # no service fee is set
    # Open until the time to pay is over, within what Stripe allows.
    open_for = asked["expires_at"] - datetime.now(timezone.utc).timestamp()
    assert 30 * 60 - 5 <= open_for <= 23 * 3600 + 5


def test_only_the_passenger_pays(users, stripe):
    booking = _booked(users)
    assert _refused(payments.checkout, booking["id"], users["jean"], BASE) == (404, "booking_not_found")
    assert _refused(payments.checkout, booking["id"], users["paul"], BASE) == (404, "booking_not_found")
    assert _refused(payments.checkout, 999, users["marie"], BASE) == (404, "booking_not_found")
    assert _refused(payments.check, booking["id"], users["paul"]) == (404, "booking_not_found")
    assert stripe.sessions == {}


def test_paying_is_recorded_once_and_the_driver_is_told(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    state = stripe.pay("cs_1")
    payload = json.dumps({"type": "checkout.session.completed", "checkout": state}).encode()
    client = TestClient(main.app)

    resp = client.post("/api/payments/webhook", content=payload, headers={"Stripe-Signature": "signed with whsec_x"})

    assert resp.status_code == 200 and resp.json() == {"received": True}
    payment = _payment(booking)
    assert (payment["status"], payment["payment_intent_id"], payment["charge_id"]) == ("paid", "pi_1", "ch_1")
    assert payment["amount_cts"] == round(booking["total_chf"] * 100)
    mine = _mine(users["marie"])[0]["payment"]
    assert (mine["status"], mine["amount_chf"], mine["pay_by"]) == ("paid", booking["total_chf"], None)
    kind, details = _inbox(users["jean"])[-1]
    assert kind == "payment_received"
    assert (details["passenger_name"], details["drive"]["origin_name"]) == ("Marie M.", "Lausanne")
    # Stripe may call again, and the passenger's page asks too: nothing new.
    client.post("/api/payments/webhook", content=payload, headers={"Stripe-Signature": "signed with whsec_x"})
    assert payments.check(booking["id"], users["marie"]) == {"status": "paid"}
    assert _kinds(users["jean"]).count("payment_received") == 1
    # Nothing left to pay.
    assert _refused(payments.checkout, booking["id"], users["marie"], BASE) == (400, "payment_not_due")


def test_a_webhook_call_not_signed_by_stripe_is_refused(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    payload = json.dumps({"type": "checkout.session.completed", "checkout": stripe.pay("cs_1")}).encode()
    client = TestClient(main.app)
    for headers in ({}, {"Stripe-Signature": "signed with something else"}):
        resp = client.post("/api/payments/webhook", content=payload, headers=headers)
        assert (resp.status_code, resp.json()["detail"]["code"]) == (400, "invalid_signature")
    assert _payment(booking)["status"] == "due"


def test_back_from_the_page_before_stripe_called(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    assert payments.check(booking["id"], users["marie"]) == {"status": "due"}
    stripe.pay("cs_1")
    assert payments.check(booking["id"], users["marie"]) == {"status": "paid"}


def test_a_new_page_closes_the_one_before(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    payments.checkout(booking["id"], users["marie"], BASE)
    assert [s["status"] for s in stripe.sessions.values()] == ["expired", "open"]

    # Paid on the page still open, without the site knowing: a third click
    # finds that out instead of opening another page.
    stripe.pay("cs_2")
    assert _refused(payments.checkout, booking["id"], users["marie"], BASE) == (400, "payment_not_due")
    assert _payment(booking)["status"] == "paid"
    assert len(stripe.sessions) == 2


def test_the_price_paid_is_the_price_at_that_moment(users, stripe):
    # At the highest price a driver may ask: the car's cost, shared.
    km = matching.haversine_km(LAUSANNE["lat"], LAUSANNE["lon"], GENEVE["lat"], GENEVE["lon"])
    drive = _drive(users["jean"], price_chf=pricing.band(km)["max_chf"])
    marie = _booked(users, "marie", drive=drive)
    alone = _mine(users["marie"])[0]["payment"]["amount_chf"]
    _booked(users, "paul", drive=drive)
    # With a second passenger, each pays less.
    shared = _mine(users["marie"])[0]["payment"]["amount_chf"]
    assert shared < alone
    _pay(stripe, marie, users["marie"])
    assert _payment(marie)["amount_cts"] == round(shared * 100)


def test_stripe_failing_opens_no_page(users, stripe):
    booking = _booked(users)
    stripe.failing.add("create_checkout")
    assert _refused(payments.checkout, booking["id"], users["marie"], BASE) == (502, "payment_failed")
    assert _payment(booking)["status"] == "due"


# --- When a booking ends before the ride ------------------------------------

def test_cancelling_a_paid_booking_refunds_it(users, stripe):
    booking = _booked(users)
    _pay(stripe, booking, users["marie"])
    paid = _payment(booking)["amount_cts"]

    booking_routes.cancel_booking(booking["id"], users["jean"])

    assert stripe.refunds == [("pi_1", paid)]
    payment = _payment(booking)
    assert (payment["refund_due_cts"], payment["refunded_cts"]) == (paid, paid)
    kind, details = _inbox(users["marie"])[-1]
    assert (kind, details["amount_chf"]) == ("payment_refunded", paid / 100)
    # The passenger still sees the booking, cancelled, and what came back.
    mine = _mine(users["marie"])[0]
    assert (mine["status"], mine["payment"]["refunded_chf"]) == ("cancelled_by_driver", paid / 100)
    # Nothing is sent twice.
    assert payments.send_refunds() == 0 and len(stripe.refunds) == 1


def test_the_passenger_cancelling_gets_it_back_too(users, stripe):
    booking = _booked(users)
    _pay(stripe, booking, users["marie"])
    booking_routes.cancel_booking(booking["id"], users["marie"])
    assert stripe.refunds == [("pi_1", _payment(booking)["amount_cts"])]


def test_cancelling_before_paying_closes_the_page(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    booking_routes.cancel_booking(booking["id"], users["marie"])
    assert _payment(booking)["status"] == "cancelled"
    assert stripe.sessions["cs_1"]["status"] == "expired"
    assert stripe.refunds == []


def test_a_payment_arriving_after_the_booking_ended_goes_back(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    # Paid on Stripe's page at the very moment the driver cancels.
    state = stripe.pay("cs_1")
    booking_routes.cancel_booking(booking["id"], users["jean"])

    payment = _payment(booking)
    assert payment["status"] == "cancelled"
    assert stripe.refunds == [("pi_1", state["amount_cts"])]
    assert (payment["refund_due_cts"], payment["refunded_cts"]) == (state["amount_cts"], state["amount_cts"])
    # The webhook call that follows changes nothing.
    payments._apply_checkout(state)
    assert len(stripe.refunds) == 1


def test_cancelling_the_drive_ends_every_payment_on_it(users, stripe):
    drive = _drive(users["jean"])
    marie = _booked(users, "marie", drive=drive)
    paul = _booked(users, "paul", drive=drive)
    _pay(stripe, marie, users["marie"])

    drive_routes.cancel_drive(drive["id"], users["jean"])

    assert stripe.refunds == [("pi_1", _payment(marie)["amount_cts"])]
    assert _payment(paul)["status"] == "cancelled"
    # The payment outlives the booking and the drive.
    assert bookings.find(marie["id"]) is None
    assert _payment(marie)["origin_name"] == "Lausanne"


def test_deleting_a_drive_after_the_ride_refunds_nobody(users, stripe, monkeypatch):
    booking = _booked(users)
    _pay(stripe, booking, users["marie"])
    after = datetime.fromisoformat(booking["pickup"]["time"]) + timedelta(hours=2)
    monkeypatch.setattr(notify, "local_now", lambda: after)

    drive_routes.cancel_drive(booking["drive_id"], users["jean"])

    assert stripe.refunds == []
    payment = _payment(booking)
    assert (payment["status"], payment["ended_at"], payment["refund_due_cts"]) == ("paid", None, 0)
    # Nor does the hourly run, which finds the booking gone.
    assert payments.run(after)["payments_ended"] == 0


def _delete_account(user):
    from fastapi import Response

    return auth_routes.delete_account(Response(), {"user": db.get_user(user["id"]), "session": {"is_demo": False}})


def test_deleting_an_account_ends_its_payments(users, stripe):
    # As a passenger...
    booking = _booked(users)
    _pay(stripe, booking, users["marie"])
    _delete_account(users["marie"])
    assert stripe.refunds == [("pi_1", _payment(booking)["amount_cts"])]
    assert _payment(booking)["passenger_id"] is None  # the record stays, without the account

    # ...and as a driver (of a later drive: one at a time).
    paul = _booked(users, "paul", drive=_drive(users["jean"], at="14:00"))
    _pay(stripe, paul, users["paul"])
    _delete_account(users["jean"])
    assert stripe.refunds[-1] == ("pi_2", _payment(paul)["amount_cts"])
    assert _payment(paul)["driver_id"] is None


def test_a_refund_that_fails_is_tried_again(users, stripe):
    booking = _booked(users)
    _pay(stripe, booking, users["marie"])
    stripe.failing.add("refund")

    booking_routes.cancel_booking(booking["id"], users["jean"])  # the cancellation itself goes through

    payment = _payment(booking)
    assert (payment["refund_due_cts"], payment["refunded_cts"]) == (payment["amount_cts"], 0)
    assert "payment_refunded" not in _kinds(users["marie"])
    stripe.failing.clear()
    assert maintenance.run(NOW)["refunds_sent"] == 1
    assert stripe.refunds == [("pi_1", payment["amount_cts"])]
    assert _kinds(users["marie"])[-1] == "payment_refunded"


def test_the_hourly_run_ends_payments_whose_booking_is_gone(users, stripe):
    booking = _booked(users)
    _pay(stripe, booking, users["marie"])
    db.delete_drive(booking["drive_id"])  # behind this module's back

    assert payments.run(NOW)["payments_ended"] == 1
    assert stripe.refunds == [("pi_1", _payment(booking)["amount_cts"])]


# --- Not paid in time ---------------------------------------------------------

def test_a_booking_not_paid_in_time_is_cancelled(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    pay_by = datetime.fromisoformat(_payment(booking)["pay_by"])

    assert payments.cancel_unpaid(pay_by - timedelta(minutes=1)) == 0
    assert payments.cancel_unpaid(pay_by) == 1

    assert _payment(booking)["status"] == "cancelled"
    assert stripe.sessions["cs_1"]["status"] == "expired"
    # The seat is free again, and both are told why.
    drive = drive_routes.get_drive(booking["drive_id"])
    assert (drive["seats_available"], drive["seats_booked"]) == (3, 0)
    kind, details = _inbox(users["jean"])[-1]
    assert (kind, details["by"], details["reason"]) == ("booking_cancelled", "passenger", "unpaid")
    assert _kinds(users["marie"])[-1] == "booking_unpaid"
    assert _mine(users["jean"], "as_driver") == []
    assert _refused(payments.checkout, booking["id"], users["marie"], BASE) == (400, "payment_not_due")
    assert payments.cancel_unpaid(pay_by) == 0


def test_too_late_to_pay(users, stripe, monkeypatch):
    booking = _booked(users)
    after = datetime.fromisoformat(_payment(booking)["pay_by"]) + timedelta(minutes=1)
    monkeypatch.setattr(notify, "local_now", lambda: after)
    assert _refused(payments.checkout, booking["id"], users["marie"], BASE) == (400, "payment_too_late")


def test_paid_without_the_site_hearing_of_it_isnt_cancelled(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    stripe.pay("cs_1")  # and no webhook call
    pay_by = datetime.fromisoformat(_payment(booking)["pay_by"])

    assert payments.cancel_unpaid(pay_by) == 0
    assert _payment(booking)["status"] == "paid"
    assert _mine(users["marie"])[0]["status"] == "accepted"


def test_stripe_unreachable_at_the_deadline_cancels_nothing(users, stripe):
    booking = _booked(users)
    payments.checkout(booking["id"], users["marie"], BASE)
    stripe.failing.add("checkout")
    assert payments.cancel_unpaid(datetime.fromisoformat(_payment(booking)["pay_by"])) == 0
    assert _payment(booking)["status"] == "due"


# --- Records ------------------------------------------------------------------

def test_records_are_deleted_after_ten_years(users, stripe):
    booking = _booked(users)
    _pay(stripe, booking, users["marie"])
    assert payments.run(NOW)["payments_deleted"] == 0
    old = (datetime.now(timezone.utc) - payments.RECORDS_KEPT - timedelta(days=1)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE payments SET created_at = ?", (old,))
    assert payments.run(NOW)["payments_deleted"] == 1


# --- What users read ----------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_emails_about_payments(lang, isolated_db):
    drive = {"origin_name": "Lausanne", "destination_name": "Genève", "departure_time": f"{TOMORROW}T08:00"}
    sentences = {}
    for kind, details in (
        ("payment_received", {"passenger_name": "Marie M."}),
        ("booking_unpaid", {}),
        ("payment_refunded", {"amount_chf": 7.5}),
        ("booking_cancelled", {"by": "passenger", "reason": "unpaid", "passenger_name": "Marie M."}),
    ):
        n = {"kind": kind, "details": {**details, "drive": drive}, "request_id": 0}
        sentences[kind], trip = notify_email._sentence(n, lang)
        assert "Lausanne → Genève" in trip
    other = translations.dictionary(lang)["email.notify.other"]
    assert other not in sentences.values()
    assert "Marie M." in sentences["payment_received"] and "Marie M." in sentences["booking_cancelled"]
    assert "7.50" in sentences["payment_refunded"]
    cancelled = translations.dictionary(lang)["email.notify.bookingCancelledByPassenger"].replace("{name}", "Marie M.")
    assert sentences["booking_cancelled"] != cancelled


def test_the_page_loads_the_script_after_bookings():
    html = (FRONTEND / "index.html").read_text()
    assert html.index('<script src="bookings.js"></script>') < html.index('<script src="payments.js"></script>')
    assert '<link rel="stylesheet" href="styles/payments.css" />' in html


@pytest.mark.parametrize("page", ["about.html", "privacy.html"])
def test_the_text_pages_say_how_a_ride_is_paid_either_way(page):
    # Both texts are in the page; payment_texts.js shows the one that is
    # true. Without it (or before it answers): the one for payments off.
    html = (FRONTEND / page).read_text()
    assert '<script src="payment_texts.js"></script>' in html
    counts = set()
    for lang in LANGS:
        article = re.search(rf'<article[^>]*lang="{lang}".*?</article>', html, re.S).group(0)
        marked = re.findall(r"<\w+ data-payments=\"(on|off)\"( hidden)?>", article)
        assert marked, (page, lang)
        for state, hidden in marked:
            assert bool(hidden) == (state == "on"), (page, lang)
        counts.add(tuple(state for state, _ in marked))
    assert len(counts) == 1  # the same in every language


def test_the_about_page_says_both():
    html = " ".join((FRONTEND / "about.html").read_text().split())
    assert '<span data-payments="off"> Nothing is paid through Ride and Rail' in html
    assert '<span data-payments="on" hidden> You pay for your seat through Ride and Rail' in html
    # The cancellation rule it gives is the code's.
    assert "until two hours before the pickup, half of it after that" in html
    assert payments.FREE_CANCEL_UNTIL == timedelta(hours=2) and payments.LATE_REFUND_SHARE == 0.5
