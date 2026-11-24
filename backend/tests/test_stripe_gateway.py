"""
The calls to Stripe as they really leave (stripe_gateway.py, through the
Stripe library), with only the network replaced: StubHTTP records each
request and answers as Stripe would. The other payment tests swap the
whole gateway for a fake, which can't tell whether payments.py calls it
the way it is written, nor what the library is asked to send; these can.
"""
import hashlib
import hmac
import json
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
import stripe
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import payments
import stripe_gateway
from routes import bookings as booking_routes
from routes import drives as drive_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
BASE = "https://rideandrail.test"
LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}


class StubHTTP(stripe.HTTPClient):
    """Stands in for the network: `requests` lists (method, path, form
    fields, idempotency key); `answers` maps (method, path) to the JSON
    Stripe would send back, or to an error's (status, JSON)."""

    name = "stub"

    def __init__(self):
        super().__init__()
        self.requests = []
        self.answers = {}

    def request(self, method, url, headers, post_data=None, *, _usage=None):
        parts = urlsplit(url)
        fields = {k: v[0] for k, v in parse_qs(post_data or parts.query).items()}
        self.requests.append((method.upper(), parts.path, fields, (headers or {}).get("Idempotency-Key")))
        answer = self.answers[(method.upper(), parts.path)]
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        return json.dumps(body), status, {"Request-Id": "req_test"}

    def close(self):
        pass

    def sent(self, method, path):
        """The form fields and idempotency key of the calls to that path."""
        return [(fields, key) for m, p, fields, key in self.requests if (m, p) == (method, path)]


def _session(session_id="cs_test_1", **fields):
    return {
        "id": session_id, "object": "checkout.session", "url": f"https://checkout.stripe.test/{session_id}",
        "status": "open", "payment_status": "unpaid", "amount_total": 1060, "payment_intent": None, "metadata": {},
        **fields,
    }


@pytest.fixture
def http(monkeypatch):
    """Payments switched on, the real gateway, a stub for the network."""
    stub = StubHTTP()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_stub")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_stub")
    monkeypatch.setitem(payments._gateways, "sk_test_stub", stripe_gateway.Gateway("sk_test_stub", http_client=stub))
    return stub


@pytest.fixture
def booking(user_named, force_haversine):
    """Marie's accepted booking on Jean's drive (accepted once payments are on)."""
    def make():
        jean, marie = user_named("Jean D."), user_named("Marie M.")
        drive = drive_routes.post_drive(drive_routes.DriveCreate(
            origin=LAUSANNE, destination=GENEVE, departure_time=f"{TOMORROW}T08:00", seats_available=3,
        ), jean)
        asked = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(), marie)
        return booking_routes.accept_booking(asked["id"], jean), marie, jean
    return make


def test_opening_the_checkout_page(http, booking):
    booked, marie, _ = booking()
    http.answers[("POST", "/v1/checkout/sessions")] = _session()

    url = payments.checkout(booked["id"], marie, BASE)

    assert url == "https://checkout.stripe.test/cs_test_1"
    (fields, _), = http.sent("POST", "/v1/checkout/sessions")
    payment = payments.for_booking(booked["id"])
    assert fields["mode"] == "payment"
    assert fields["line_items[0][price_data][currency]"] == "chf"
    assert fields["line_items[0][price_data][unit_amount]"] == str(round(booked["total_chf"] * 100))
    assert fields["line_items[0][price_data][product_data][name]"].startswith("Ride Lausanne → Genève")
    assert fields["line_items[0][quantity]"] == "1"
    assert fields["success_url"] == f"{BASE}/?paid={booked['id']}#/ride"
    assert fields["cancel_url"] == f"{BASE}/?unpaid={booked['id']}#/ride"
    assert fields["metadata[payment_id]"] == fields["payment_intent_data[metadata][payment_id]"] == str(payment["id"])
    assert fields["locale"] == "en"  # the test users have no language of their own
    assert int(fields["expires_at"]) > time.time()
    # Which payment methods the page offers is the dashboard's business.
    assert not [name for name in fields if name.startswith("payment_method_types")]
    assert payment["checkout_session_id"] == "cs_test_1"


def test_a_paid_page_is_recorded_and_refunded_with_a_key(http, booking):
    booked, marie, jean = booking()
    http.answers[("POST", "/v1/checkout/sessions")] = _session()
    payments.checkout(booked["id"], marie, BASE)
    payment_id = str(payments.for_booking(booked["id"])["id"])
    http.answers[("GET", "/v1/checkout/sessions/cs_test_1")] = _session(
        status="complete", payment_status="paid", payment_intent="pi_test_1", metadata={"payment_id": payment_id},
    )
    http.answers[("GET", "/v1/payment_intents/pi_test_1")] = {
        "id": "pi_test_1", "object": "payment_intent", "latest_charge": "ch_test_1",
    }

    assert payments.check(booked["id"], marie) == {"status": "paid"}

    payment = payments.for_booking(booked["id"])
    assert (payment["payment_intent_id"], payment["charge_id"], payment["amount_cts"]) == ("pi_test_1", "ch_test_1", 1060)

    # The driver cancels: the refund names the payment, the amount, and itself.
    http.answers[("POST", "/v1/refunds")] = {"id": "re_test_1", "object": "refund"}
    booking_routes.cancel_booking(booked["id"], jean)
    (fields, key), = http.sent("POST", "/v1/refunds")
    assert fields == {"payment_intent": "pi_test_1", "amount": "1060"}
    assert key == f"refund-{payment_id}-0-1060"
    assert payments.for_booking(booked["id"])["refunded_cts"] == 1060


def test_closing_a_page(http, booking):
    booked, marie, _ = booking()
    http.answers[("POST", "/v1/checkout/sessions")] = _session()
    payments.checkout(booked["id"], marie, BASE)
    http.answers[("POST", "/v1/checkout/sessions/cs_test_1/expire")] = _session(status="expired")

    booking_routes.cancel_booking(booked["id"], marie)

    assert len(http.sent("POST", "/v1/checkout/sessions/cs_test_1/expire")) == 1
    assert payments.for_booking(booked["id"])["status"] == "cancelled"


def test_stripe_refusing_is_an_error_the_page_can_show(http, booking):
    booked, marie, _ = booking()
    http.answers[("POST", "/v1/checkout/sessions")] = (400, {"error": {"type": "invalid_request_error", "message": "No"}})
    with pytest.raises(HTTPException) as exc:
        payments.checkout(booked["id"], marie, BASE)
    assert (exc.value.status_code, exc.value.detail["code"]) == (502, "payment_failed")


def _signed(payload: bytes, secret: str, at: int | None = None) -> str:
    """Stripe's signature header for a webhook call."""
    at = at or int(time.time())
    digest = hmac.new(secret.encode(), f"{at}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={at},v1={digest}"


def test_the_webhook_checks_stripes_signature(http, booking):
    booked, marie, _ = booking()
    http.answers[("POST", "/v1/checkout/sessions")] = _session()
    payments.checkout(booked["id"], marie, BASE)
    http.answers[("GET", "/v1/payment_intents/pi_test_1")] = {
        "id": "pi_test_1", "object": "payment_intent", "latest_charge": "ch_test_1",
    }
    payment_id = str(payments.for_booking(booked["id"])["id"])
    payload = json.dumps({
        "id": "evt_test_1", "object": "event", "type": "checkout.session.completed",
        "data": {"object": _session(
            status="complete", payment_status="paid", payment_intent="pi_test_1", metadata={"payment_id": payment_id},
        )},
    }).encode()
    client = TestClient(main.app)

    # Signed with another secret, signed long ago, or not at all: refused.
    for signature in (_signed(payload, "whsec_other"), _signed(payload, "whsec_stub", int(time.time()) - 3600), None):
        headers = {"Stripe-Signature": signature} if signature else {}
        resp = client.post("/api/payments/webhook", content=payload, headers=headers)
        assert (resp.status_code, resp.json()["detail"]["code"]) == (400, "invalid_signature")
    assert payments.for_booking(booked["id"])["status"] == "due"

    resp = client.post("/api/payments/webhook", content=payload, headers={"Stripe-Signature": _signed(payload, "whsec_stub")})
    assert resp.status_code == 200
    assert payments.for_booking(booked["id"])["status"] == "paid"
