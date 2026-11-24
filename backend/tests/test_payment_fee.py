"""
The service fee (payments.py: fee_cts, docs/payments.md): none unless it
is set; when it is, added to what the passenger pays on a line of its own,
shown before paying, kept out of what the driver is sent, and given back
whenever the ride's price is given back in full. Stripe is a fake
(fake_stripe.py); isolated temp DB and haversine routing.
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import matching
import notify
import payments
import payouts
import pricing
import stripe_gateway
from fake_stripe import NOW, at_noon, stripe  # noqa: F401 (fixtures)
from routes import bookings as booking_routes
from routes import drives as drive_routes
from test_stripe_gateway import StubHTTP, _session

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
LANGS = ["en", "de", "fr", "it"]
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
PICKUP = datetime.fromisoformat(f"{TOMORROW}T08:00")
BASE = "https://rideandrail.test"

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}


@pytest.fixture
def fee(monkeypatch):
    """A service fee of 10 % plus 50 centimes (numbers for the tests only)."""
    monkeypatch.setenv("SERVICE_FEE_PERCENT", "10")
    monkeypatch.setenv("SERVICE_FEE_FIXED_CHF", "0.50")


@pytest.fixture(autouse=True)
def no_fee_by_default(monkeypatch):
    for name in payments.FEE_SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def users(user_named, force_haversine):
    return {name.split()[0].lower(): user_named(name) for name in ("Jean D.", "Marie M.", "Paul P.")}


def _drive(users, **fields):
    return drive_routes.post_drive(drive_routes.DriveCreate(
        origin=LAUSANNE, destination=GENEVE, departure_time=f"{TOMORROW}T08:00", seats_available=3, **fields,
    ), users["jean"])


def _accepted(users, passenger="marie", drive=None):
    drive = drive or _drive(users)
    asked = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(), users[passenger])
    return booking_routes.accept_booking(asked["id"], users["jean"])


def _pay(users, stripe, booking, passenger="marie"):
    payments.checkout(booking["id"], users[passenger], BASE)
    payments._apply_checkout(stripe.pay(stripe.last()))
    return payments.for_booking(booking["id"])


def _set_up_payouts(users, stripe):
    payouts.onboard(users["jean"], BASE)
    stripe.approve("acct_1")
    payouts.refresh(users["jean"])


def _shown(user):
    return booking_routes.get_bookings(user)["as_passenger"][0]["payment"]


# --- The fee itself -----------------------------------------------------------

def test_no_fee_unless_it_is_set(stripe):
    assert payments.fee_settings() is None
    assert payments.fee_cts(1060) == 0
    assert TestClient(main.app).get("/api/payments/config").json() == {"enabled": True, "fee": None}


def test_a_share_plus_a_fixed_part_rounded_to_five_centimes(stripe, fee):
    assert payments.fee_settings() == {"percent": 10.0, "fixed_cts": 50}
    assert TestClient(main.app).get("/api/payments/config").json() == {
        "enabled": True, "fee": {"percent": 10.0, "fixed_cts": 50},
    }
    # 10 % of 10.60 is 1.06, plus 0.50: 1.56, which rounds to 1.55.
    assert payments.fee_cts(1060) == 155
    assert payments.fee_cts(1080) == 160  # 1.58 rounds up
    assert payments.fee_cts(450) == 95
    assert payments.fee_cts(0) == 0  # a ride that costs nothing has no fee
    assert all(payments.fee_cts(ride) % payments.FEE_STEP_CTS == 0 for ride in range(1, 5000, 7))


def test_only_one_part_set(stripe, monkeypatch):
    monkeypatch.setenv("SERVICE_FEE_FIXED_CHF", "1")
    assert payments.fee_settings() == {"percent": 0.0, "fixed_cts": 100}
    assert payments.fee_cts(1060) == 100


@pytest.mark.parametrize("name, value", [
    ("SERVICE_FEE_PERCENT", "ten"), ("SERVICE_FEE_PERCENT", "-5"), ("SERVICE_FEE_PERCENT", "500"),
    ("SERVICE_FEE_FIXED_CHF", "1,50"), ("SERVICE_FEE_FIXED_CHF", "150"),
])
def test_a_mistyped_setting_means_no_fee_not_a_broken_site(stripe, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert payments.fee_settings() is None
    assert payments.fee_cts(1060) == 0


def test_the_fee_isnt_announced_while_payments_are_off(fee):
    assert TestClient(main.app).get("/api/payments/config").json() == {"enabled": False, "fee": None}


def test_the_page_computes_the_fee_as_the_server_does():
    # payments.js shows it next to the prices of a search; same arithmetic.
    script = (FRONTEND / "payments.js").read_text()
    assert "Math.floor((rideCts * fee.percent / 100 + fee.fixed_cts) / 5 + 0.5) * 5" in script
    assert payments.FEE_STEP_CTS == 5


# --- Paying with a fee --------------------------------------------------------

def test_the_fee_is_shown_before_paying_and_on_its_own_line(users, stripe, fee):
    booking = _accepted(users)
    ride = round(booking["total_chf"] * 100)
    shown = _shown(users["marie"])
    assert (shown["amount_chf"], shown["fee_chf"]) == ((ride + payments.fee_cts(ride)) / 100, payments.fee_cts(ride) / 100)

    payments.checkout(booking["id"], users["marie"], BASE)

    asked = stripe.sessions["cs_1"]["asked"]
    assert (asked["amount_cts"], asked["fee_cts"], asked["fee_description"]) == (ride, payments.fee_cts(ride), "Service fee")
    assert asked["metadata"]["fee_cts"] == str(payments.fee_cts(ride))
    payments._apply_checkout(stripe.pay("cs_1"))
    payment = payments.for_booking(booking["id"])
    assert (payment["amount_cts"], payment["fee_cts"]) == (ride + payments.fee_cts(ride), payments.fee_cts(ride))
    shown = _shown(users["marie"])
    assert (shown["amount_chf"], shown["fee_chf"]) == (payment["amount_cts"] / 100, payment["fee_cts"] / 100)


def test_the_real_checkout_call_has_two_lines(users, fee, monkeypatch):
    stub = StubHTTP()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_stub")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_stub")
    monkeypatch.setitem(payments._gateways, "sk_test_stub", stripe_gateway.Gateway("sk_test_stub", http_client=stub))
    booking = _accepted(users)
    stub.answers[("POST", "/v1/checkout/sessions")] = _session()

    payments.checkout(booking["id"], users["marie"], BASE)

    (fields, _), = stub.sent("POST", "/v1/checkout/sessions")
    ride = round(booking["total_chf"] * 100)
    assert fields["line_items[0][price_data][unit_amount]"] == str(ride)
    assert fields["line_items[1][price_data][unit_amount]"] == str(payments.fee_cts(ride))
    assert fields["line_items[1][price_data][product_data][name]"] == "Service fee"
    assert fields["metadata[fee_cts]"] == str(payments.fee_cts(ride))


def test_the_driver_is_sent_the_rides_price_without_the_fee(users, stripe, fee):
    _set_up_payouts(users, stripe)
    booking = _accepted(users)
    payment = _pay(users, stripe, booking)
    ride = payment["amount_cts"] - payment["fee_cts"]
    assert payouts.status(users["jean"])["waiting_chf"] == ride / 100

    after = datetime.fromisoformat(booking["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    assert payouts.pay_out(after) == {"sent": 1, "given_back": 0}
    assert stripe.transfers == [("acct_1", ride, "ch_1")]


# --- The fee when money goes back ---------------------------------------------

def test_a_full_refund_includes_the_fee(users, stripe, fee):
    booking = _accepted(users)
    payment = _pay(users, stripe, booking)
    booking_routes.cancel_booking(booking["id"], users["jean"])
    assert stripe.refunds == [("pi_1", payment["amount_cts"])]


def test_a_late_cancellation_refunds_half_the_ride_and_keeps_the_fee(users, stripe, fee, monkeypatch):
    _set_up_payouts(users, stripe)
    booking = _accepted(users)
    payment = _pay(users, stripe, booking)
    ride = payment["amount_cts"] - payment["fee_cts"]
    monkeypatch.setattr(notify, "local_now", lambda: PICKUP - timedelta(hours=1))
    assert _shown(users["marie"])["refund_if_cancelled_chf"] == (ride // 2) / 100

    booking_routes.cancel_booking(booking["id"], users["marie"])

    assert stripe.refunds == [("pi_1", ride // 2)]
    after = datetime.fromisoformat(booking["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    payouts.pay_out(after)
    # The driver gets the other half of the ride; the fee is neither's.
    assert stripe.transfers == [("acct_1", ride - ride // 2, "ch_1")]


def test_the_fee_follows_the_price_down_when_the_ride_starts(users, stripe, fee):
    _set_up_payouts(users, stripe)
    km = matching.haversine_km(LAUSANNE["lat"], LAUSANNE["lon"], GENEVE["lat"], GENEVE["lon"])
    drive = _drive(users, price_chf=pricing.band(km)["max_chf"])
    marie = _accepted(users, "marie", drive)
    alone = _pay(users, stripe, marie)
    paul = _accepted(users, "paul", drive)
    shared = _pay(users, stripe, paul, "paul")
    assert shared["amount_cts"] < alone["amount_cts"] and shared["fee_cts"] < alone["fee_cts"]

    assert payments.settle_prices(PICKUP) == 1

    # Marie ends up having paid what Paul paid: the ride and its fee.
    assert stripe.refunds == [("pi_1", alone["amount_cts"] - shared["amount_cts"])]
    assert payments.for_booking(marie["id"])["fee_cts"] == shared["fee_cts"]
    after = datetime.fromisoformat(marie["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    payouts.pay_out(after)
    ride = shared["amount_cts"] - shared["fee_cts"]
    assert [amount for _, amount, _ in stripe.transfers] == [ride, ride]


def test_money_nobody_claimed_goes_back_with_the_fee(users, stripe, fee):
    booking = _accepted(users)
    payment = _pay(users, stripe, booking)
    late = datetime.fromisoformat(booking["dropoff"]["time"]) + payouts.UNCLAIMED_AFTER
    assert payouts.pay_out(late) == {"sent": 0, "given_back": 1}
    assert stripe.refunds == [("pi_1", payment["amount_cts"])]


# --- "Your payments" ----------------------------------------------------------

def test_what_the_account_page_lists(users, stripe, fee):
    _set_up_payouts(users, stripe)
    booking = _accepted(users)
    assert payments.history(users["marie"]) == {"paid": [], "received": []}
    payment = _pay(users, stripe, booking)
    after = datetime.fromisoformat(booking["dropoff"]["time"]) + payouts.PAY_OUT_AFTER
    payouts.pay_out(after)

    paid, = payments.history(users["marie"])["paid"]
    assert paid == {
        "id": payment["id"], "origin_name": "Lausanne", "destination_name": "Genève", "pickup_time": f"{TOMORROW}T08:00",
        "seats": 1, "paid_at": payment["paid_at"], "amount_chf": payment["amount_cts"] / 100,
        "fee_chf": payment["fee_cts"] / 100, "refunded_chf": 0.0, "reference": "pi_1",
    }
    received, = payments.history(users["jean"])["received"]
    assert received["amount_chf"] == (payment["amount_cts"] - payment["fee_cts"]) / 100
    # Each sees their own side only.
    assert payments.history(users["marie"])["received"] == [] and payments.history(users["jean"])["paid"] == []
    assert payments.history(users["paul"]) == {"paid": [], "received": []}
    assert TestClient(main.app).get("/api/payments").status_code == 401


# --- What users read ----------------------------------------------------------

def test_the_about_page_mentions_the_fee_only_when_there_is_one():
    html = (FRONTEND / "about.html").read_text()
    for lang in LANGS:
        article = re.search(rf'<article[^>]*lang="{lang}".*?</article>', html, re.S).group(0)
        assert len(re.findall(r'<span data-payments="fee" hidden>', article)) == 1, lang
    assert "Ride and Rail adds a service fee to the price; you see it before you pay." in " ".join(html.split())
    script = (FRONTEND / "payment_texts.js").read_text()
    assert 'when === "fee" ? state !== "fee"' in script
