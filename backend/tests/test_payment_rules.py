"""
The rules around a paid booking (payments.py, docs/payments.md): what comes
back when it is cancelled, by whom and when; the price settled when the
ride starts; and "the ride didn't take place", which puts the payment on
hold for the team. Stripe is a fake (fake_stripe.py); isolated temp DB and
haversine routing.
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

import admin
import database as db
import matching
import notify
import notify_email
import payments
import payouts
import pricing
import reports
import translations
from fake_stripe import NOW, at_noon, stripe  # noqa: F401 (fixtures)
from routes import auth as auth_routes
from routes import bookings as booking_routes
from routes import drives as drive_routes

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
LANGS = ["en", "de", "fr", "it"]
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
PICKUP = datetime.fromisoformat(f"{TOMORROW}T08:00")
BASE = "https://rideandrail.test"

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}


@pytest.fixture
def users(user_named, force_haversine):
    """Jean drives; Marie and Paul ride."""
    return {name.split()[0].lower(): user_named(name) for name in ("Jean D.", "Marie M.", "Paul P.")}


@pytest.fixture
def at(monkeypatch):
    """at(moment): it is that moment from now on."""
    def set_now(moment):
        monkeypatch.setattr(notify, "local_now", lambda: moment)
    return set_now


def _drive(users, **fields):
    return drive_routes.post_drive(drive_routes.DriveCreate(
        origin=LAUSANNE, destination=GENEVE, departure_time=f"{TOMORROW}T08:00", seats_available=3, **fields,
    ), users["jean"])


def _dear_drive(users):
    """A drive at the highest price its driver may ask (the car's cost,
    shared): the price per seat then falls with a second passenger."""
    km = matching.haversine_km(LAUSANNE["lat"], LAUSANNE["lon"], GENEVE["lat"], GENEVE["lon"])
    return _drive(users, price_chf=pricing.band(km)["max_chf"])


def _accepted(users, passenger, drive):
    asked = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(), users[passenger])
    return booking_routes.accept_booking(asked["id"], users["jean"])


def _pay(users, stripe, passenger, booking):
    payments.checkout(booking["id"], users[passenger], BASE)
    payments._apply_checkout(stripe.pay(stripe.last()))
    return payments.for_booking(booking["id"])["amount_cts"]


def _paid(users, stripe, passenger="marie", drive=None):
    """(the booking, the centimes paid for it)"""
    booking = _accepted(users, passenger, drive or _drive(users))
    return booking, _pay(users, stripe, passenger, booking)


def _set_up_payouts(users, stripe):
    payouts.onboard(users["jean"], BASE)
    stripe.approve("acct_1")
    payouts.refresh(users["jean"])


def _refused(call, *args, **kwargs):
    with pytest.raises(HTTPException) as exc:
        call(*args, **kwargs)
    return exc.value.status_code, exc.value.detail["code"]


def _inbox(user):
    return [(n["kind"], n["details"]) for n in reversed(notify.inbox(user["name"])["notifications"])]


def _kinds(user):
    return [kind for kind, _ in _inbox(user)]


def _shown(user):
    return booking_routes.get_bookings(user)["as_passenger"][0]["payment"]


# --- What comes back when a booking is cancelled -----------------------------

def test_the_passenger_sees_what_cancelling_would_cost(users, stripe, at):
    booking, paid = _paid(users, stripe)
    shown = _shown(users["marie"])
    assert shown["free_cancel_until"] == f"{TOMORROW}T06:00"
    assert shown["refund_if_cancelled_chf"] == paid / 100

    at(PICKUP - timedelta(hours=1))
    assert _shown(users["marie"])["refund_if_cancelled_chf"] == (paid // 2) / 100
    at(PICKUP)
    assert _shown(users["marie"])["refund_if_cancelled_chf"] == 0.0


def test_cancelling_in_time_refunds_everything(users, stripe, at):
    booking, paid = _paid(users, stripe)
    at(PICKUP - payments.FREE_CANCEL_UNTIL)
    booking_routes.cancel_booking(booking["id"], users["marie"])
    assert stripe.refunds == [("pi_1", paid)]


def test_cancelling_late_refunds_half_and_the_driver_gets_the_rest(users, stripe, at):
    _set_up_payouts(users, stripe)
    booking, paid = _paid(users, stripe)
    at(PICKUP - payments.FREE_CANCEL_UNTIL + timedelta(minutes=1))

    booking_routes.cancel_booking(booking["id"], users["marie"])

    half = int(paid * payments.LATE_REFUND_SHARE)
    assert stripe.refunds == [("pi_1", half)]
    assert payouts.status(users["jean"])["waiting_chf"] == (paid - half) / 100
    # Sent with the rest of that ride's money, a day after its time.
    after = datetime.fromisoformat(booking["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    assert payouts.pay_out(after) == {"sent": 1, "given_back": 0}
    assert stripe.transfers == [("acct_1", paid - half, "ch_1")]


def test_cancelling_after_the_pickup_time_refunds_nothing(users, stripe, at):
    _set_up_payouts(users, stripe)
    booking, paid = _paid(users, stripe)
    at(PICKUP + timedelta(minutes=5))
    booking_routes.cancel_booking(booking["id"], users["marie"])
    assert stripe.refunds == []
    after = datetime.fromisoformat(booking["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    assert payouts.pay_out(after) == {"sent": 1, "given_back": 0}
    assert stripe.transfers == [("acct_1", paid, "ch_1")]


def test_the_driver_cancelling_late_still_refunds_everything(users, stripe, at):
    booking, paid = _paid(users, stripe)
    at(PICKUP - timedelta(minutes=10))
    booking_routes.cancel_booking(booking["id"], users["jean"])
    assert stripe.refunds == [("pi_1", paid)]


def test_so_does_cancelling_the_drive(users, stripe, at):
    booking, paid = _paid(users, stripe)
    at(PICKUP - timedelta(minutes=10))
    drive_routes.cancel_drive(booking["drive_id"], users["jean"])
    assert stripe.refunds == [("pi_1", paid)]


def test_a_passenger_deleting_their_account_late_is_a_late_cancellation(users, stripe, at):
    booking, paid = _paid(users, stripe)
    at(PICKUP - timedelta(minutes=30))
    auth_routes.delete_account(Response(), {"user": db.get_user(users["marie"]["id"]), "session": {"is_demo": False}})
    assert stripe.refunds == [("pi_1", paid // 2)]


# --- The price when the ride starts ------------------------------------------

def test_what_was_paid_above_the_final_price_is_refunded_when_the_ride_starts(users, stripe):
    _set_up_payouts(users, stripe)
    drive = _dear_drive(users)
    marie, alone = _paid(users, stripe, "marie", drive)
    paul, shared = _paid(users, stripe, "paul", drive)
    assert shared < alone  # with two passengers, each pays less

    assert payments.settle_prices(PICKUP - timedelta(minutes=1)) == 0
    assert payments.settle_prices(PICKUP) == 1

    # Marie, who paid when she was alone, gets the difference back; Paul paid the final price.
    assert stripe.refunds == [("pi_1", alone - shared)]
    kind, details = _inbox(users["marie"])[-1]
    assert (kind, details["reason"], details["amount_chf"]) == ("payment_refunded", "shared", (alone - shared) / 100)
    assert "payment_refunded" not in _kinds(users["paul"])
    assert payments.settle_prices(PICKUP + timedelta(hours=1)) == 0  # once
    # The driver receives the final price from each.
    after = datetime.fromisoformat(marie["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    assert payouts.pay_out(after) == {"sent": 2, "given_back": 0}
    assert [amount for _, amount, _ in stripe.transfers] == [shared, shared]


def test_nobody_is_asked_for_more_when_the_price_rises(users, stripe, at):
    drive = _dear_drive(users)
    marie = _accepted(users, "marie", drive)
    paul = _accepted(users, "paul", drive)
    shared = _pay(users, stripe, "marie", marie)  # at the price for two
    booking_routes.cancel_booking(paul["id"], users["paul"])  # then Paul leaves

    assert payments.settle_prices(PICKUP) == 0
    payment = payments.for_booking(marie["id"])
    assert (payment["amount_cts"], payment["refund_due_cts"]) == (shared, 0)
    assert payment["settled_at"]


def test_the_hourly_run_settles_before_paying_out(users, stripe):
    _set_up_payouts(users, stripe)
    drive = _dear_drive(users)
    marie, alone = _paid(users, stripe, "marie", drive)
    paul, shared = _paid(users, stripe, "paul", drive)
    # Nothing ran between the pickup and the payout.
    after = datetime.fromisoformat(marie["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    assert payouts.pay_out(after) == {"sent": 2, "given_back": 0}
    assert [amount for _, amount, _ in stripe.transfers] == [shared, shared]
    assert stripe.refunds == [("pi_1", alone - shared)]


# --- "The ride didn't take place" ----------------------------------------------

def test_the_passenger_puts_the_payment_on_hold(users, stripe, at):
    _set_up_payouts(users, stripe)
    booking, paid = _paid(users, stripe)
    # Not before the pickup time, and only the passenger.
    assert _shown(users["marie"])["can_dispute"] is False
    assert _refused(payments.dispute, booking["id"], users["marie"]) == (400, "dispute_not_possible")
    at(PICKUP + timedelta(minutes=30))
    assert _shown(users["marie"])["can_dispute"] is True
    assert _refused(payments.dispute, booking["id"], users["jean"]) == (404, "booking_not_found")

    assert payments.dispute(booking["id"], users["marie"], "Nobody came") == {"status": "paid", "on_hold": True}

    shown = _shown(users["marie"])
    assert (shown["on_hold"], shown["can_dispute"]) == (True, False)
    assert booking_routes.get_bookings(users["jean"])["as_driver"][0]["payment"]["on_hold"] is True
    kind, details = _inbox(users["jean"])[-1]
    assert (kind, details["passenger_name"]) == ("payout_held", "Marie M.")
    # The team has a report to look at, and the payment in its list.
    report = reports.open_reports()[0]
    assert (report["reason"], report["reporter_name"], report["reported_name"]) == ("no_show", "Marie M.", "Jean D.")
    assert "Nobody came" in report["details"] and "on hold" in report["details"]
    assert [p["booking_id"] for p in payments.on_hold()] == [booking["id"]]
    # Once.
    assert _refused(payments.dispute, booking["id"], users["marie"]) == (400, "dispute_not_possible")
    # The driver isn't paid while it's on hold, however long.
    assert payouts.pay_out(PICKUP + timedelta(days=90)) == {"sent": 0, "given_back": 0}
    assert stripe.transfers == [] and stripe.refunds == []


def test_the_team_refunds_it(users, stripe, at, capsys):
    _set_up_payouts(users, stripe)
    booking, paid = _paid(users, stripe)
    at(PICKUP + timedelta(minutes=30))
    payments.dispute(booking["id"], users["marie"])
    payment_id = payments.for_booking(booking["id"])["id"]

    assert admin.main(["payments"]) == 0
    listed = capsys.readouterr().out
    assert f"Payment #{payment_id} on hold" in listed and "Lausanne → Genève" in listed and "Marie M." in listed
    assert admin.main(["refund-payment", str(payment_id)]) == 0

    assert stripe.refunds == [("pi_1", paid)]
    assert _kinds(users["jean"])[-1] == "payout_refunded"
    assert _kinds(users["marie"])[-1] == "payment_refunded"
    assert payments.on_hold() == []
    assert payouts.pay_out(PICKUP + timedelta(days=3)) == {"sent": 0, "given_back": 0}
    # Decided once.
    assert admin.main(["release-payment", str(payment_id)]) == 1
    assert admin.main(["payments"]) == 0
    assert "No payments on hold." in capsys.readouterr().out


def test_the_team_releases_it_to_the_driver(users, stripe, at):
    _set_up_payouts(users, stripe)
    booking, paid = _paid(users, stripe)
    at(PICKUP + timedelta(minutes=30))
    payments.dispute(booking["id"], users["marie"])

    assert admin.main(["release-payment", str(payments.for_booking(booking["id"])["id"])]) == 0

    assert _kinds(users["marie"])[-1] == "payment_released"
    assert payouts.pay_out(PICKUP + timedelta(days=3)) == {"sent": 1, "given_back": 0}
    assert stripe.transfers == [("acct_1", paid, "ch_1")] and stripe.refunds == []


def test_too_late_once_the_driver_is_paid(users, stripe, at):
    _set_up_payouts(users, stripe)
    booking, paid = _paid(users, stripe)
    after = datetime.fromisoformat(booking["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    payouts.pay_out(after)
    at(after)
    assert _refused(payments.dispute, booking["id"], users["marie"]) == (400, "dispute_not_possible")


def test_admin_refuses_what_isnt_on_hold(users, stripe):
    booking, _ = _paid(users, stripe)
    assert admin.main(["refund-payment", str(payments.for_booking(booking["id"])["id"])]) == 1
    assert admin.main(["refund-payment", "nonsense"]) == 1


# --- What users read ----------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_emails_about_these_rules(lang, isolated_db):
    drive = {"origin_name": "Lausanne", "destination_name": "Genève", "departure_time": f"{TOMORROW}T08:00"}
    words = translations.dictionary(lang)
    sentences = set()
    for kind, details in (
        ("payment_refunded", {"amount_chf": 3.7}),
        ("payment_refunded", {"amount_chf": 3.7, "reason": "shared"}),
        ("payout_held", {"passenger_name": "Marie M."}),
        ("payment_released", {}),
        ("payout_refunded", {}),
    ):
        sentence, trip = notify_email._sentence({"kind": kind, "details": {**details, "drive": drive}, "request_id": 0}, lang)
        assert sentence != words["email.notify.other"] and "Lausanne → Genève" in trip
        sentences.add(sentence)
    assert len(sentences) == 5


def test_the_page_and_the_rule_agree_on_the_hours():
    script = (FRONTEND / "payments.js").read_text()
    hours = int(re.search(r"const FREE_CANCEL_HOURS = (\d+);", script).group(1))
    assert timedelta(hours=hours) == payments.FREE_CANCEL_UNTIL
