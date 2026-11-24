"""
The calls to Stripe that payments.py makes, in one place: each takes and
returns plain values, so the rest of the code (and the tests, which swap
this class for a fake) never touches Stripe's own objects.

Amounts are in centimes (Rappen), the currency is CHF. Calls that move
money take an idempotency key: repeating one after a timeout or a crash
doesn't move the money twice.
"""
import stripe

CURRENCY = "chf"
# Stripe's Checkout page speaks these; another language gets its "auto".
CHECKOUT_LOCALES = {"en", "de", "fr", "it"}

# What callers catch: Stripe refused or couldn't be reached.
Error = stripe.StripeError
SignatureError = stripe.SignatureVerificationError


class Gateway:
    def __init__(self, secret_key: str):
        self._client = stripe.StripeClient(secret_key, max_network_retries=2)

    def create_checkout(self, *, amount_cts: int, description: str, success_url: str, cancel_url: str,
                        expires_at: int, metadata: dict, idempotency_key: str, language: str | None = None,
                        email: str | None = None) -> dict:
        """A Checkout page for one payment of `amount_cts`; {"id", "url"}.
        The payment methods offered are those switched on in the Stripe
        dashboard (cards, TWINT, Apple Pay, Google Pay...)."""
        params = {
            "mode": "payment",
            "line_items": [{
                "quantity": 1,
                "price_data": {
                    "currency": CURRENCY, "unit_amount": amount_cts, "product_data": {"name": description},
                },
            }],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": expires_at,
            "metadata": metadata,
            "payment_intent_data": {"metadata": metadata, "description": description},
            "locale": language if language in CHECKOUT_LOCALES else "auto",
        }
        if email:
            params["customer_email"] = email
        session = self._client.v1.checkout.sessions.create(params, {"idempotency_key": idempotency_key})
        return {"id": session.id, "url": session.url}

    def checkout(self, session_id: str) -> dict:
        """The Checkout page's state: {"id", "status" ('open', 'complete' or
        'expired'), "paid", "amount_cts", "payment_intent", "metadata"}."""
        return _checkout(self._client.v1.checkout.sessions.retrieve(session_id))

    def expire_checkout(self, session_id: str) -> None:
        """Close an open Checkout page, so that it can't be paid any more."""
        self._client.v1.checkout.sessions.expire(session_id)

    def charge_of(self, payment_intent_id: str) -> str | None:
        """The charge a completed payment made: what a refund or a transfer
        refers to."""
        intent = self._client.v1.payment_intents.retrieve(payment_intent_id)
        charge = intent.latest_charge
        return charge if isinstance(charge, str) or charge is None else charge.id

    def refund(self, payment_intent_id: str, amount_cts: int, idempotency_key: str) -> str:
        """Give `amount_cts` of a payment back to the payer; the refund's id."""
        refund = self._client.v1.refunds.create(
            {"payment_intent": payment_intent_id, "amount": amount_cts}, {"idempotency_key": idempotency_key},
        )
        return refund.id

    def event(self, payload: bytes, signature: str | None, secret: str) -> dict:
        """A webhook call from Stripe, its signature checked: {"type",
        "checkout"} (the latter for the events about a Checkout page, else
        None). Raises SignatureError when it isn't Stripe's."""
        event = self._client.construct_event(payload, signature, secret)
        is_checkout = event.type.startswith("checkout.session.")
        return {"type": event.type, "checkout": _checkout(event.data.object) if is_checkout else None}


def _checkout(session) -> dict:
    intent = session.payment_intent
    return {
        "id": session.id,
        "status": session.status,
        "paid": session.payment_status == "paid",
        "amount_cts": session.amount_total,
        "payment_intent": intent if isinstance(intent, str) or intent is None else intent.id,
        "metadata": dict(session.metadata or {}),
    }
