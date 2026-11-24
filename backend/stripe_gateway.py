"""
The calls to Stripe that payments.py and payouts.py make, in one place: each takes and
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
    def __init__(self, secret_key: str, http_client=None):
        # `http_client`: the tests' stand-in for the network.
        self._client = stripe.StripeClient(secret_key, max_network_retries=2, http_client=http_client)

    def create_checkout(self, *, amount_cts: int, description: str, success_url: str, cancel_url: str,
                        expires_at: int, metadata: dict, language: str | None = None, email: str | None = None,
                        fee_cts: int = 0, fee_description: str = "") -> dict:
        """A Checkout page for one payment of `amount_cts`, plus `fee_cts`
        on a line of its own when there is a service fee; {"id", "url"}.
        The payment methods offered are those switched on in the Stripe
        dashboard (cards, TWINT, Apple Pay, Google Pay...)."""
        lines = [(description, amount_cts)] + ([(fee_description, fee_cts)] if fee_cts else [])
        params = {
            "mode": "payment",
            "line_items": [
                {"quantity": 1, "price_data": {"currency": CURRENCY, "unit_amount": cents, "product_data": {"name": name}}}
                for name, cents in lines
            ],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": expires_at,
            "metadata": metadata,
            "payment_intent_data": {"metadata": metadata, "description": description},
            "locale": language if language in CHECKOUT_LOCALES else "auto",
        }
        if email:
            params["customer_email"] = email
        session = self._client.v1.checkout.sessions.create(params)
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

    # --- Drivers' accounts and what is sent to them (payouts.py) ---

    def create_account(self, *, email: str | None, description: str, metadata: dict) -> str:
        """A connected account for a driver, who then fills it in on
        Stripe's own pages (onboarding_link); its id. It only receives
        transfers; Stripe pays its balance out to the bank account weekly."""
        params = {
            "type": "express",
            "country": "CH",
            "business_type": "individual",
            "capabilities": {"transfers": {"requested": True}},
            "business_profile": {"product_description": description, "mcc": "4789"},
            "settings": {"payouts": {"schedule": {"interval": "weekly", "weekly_anchor": "monday"}}},
            "metadata": metadata,
        }
        if email:
            params["email"] = email
        return self._client.v1.accounts.create(params).id

    def onboarding_link(self, account_id: str, return_url: str, refresh_url: str) -> str:
        """The address of Stripe's pages where the driver gives their
        identity and bank account; valid a few minutes, once."""
        link = self._client.v1.account_links.create({
            "account": account_id, "type": "account_onboarding", "return_url": return_url, "refresh_url": refresh_url,
        })
        return link.url

    def account(self, account_id: str) -> dict:
        """{"submitted": the driver went through Stripe's pages, "ready":
        Stripe accepted them, so transfers and payouts work}."""
        account = self._client.v1.accounts.retrieve(account_id)
        capabilities = account.capabilities
        transfers = getattr(capabilities, "transfers", None) if capabilities is not None else None
        return {
            "submitted": bool(account.details_submitted),
            "ready": bool(account.payouts_enabled) and transfers == "active",
        }

    def dashboard_link(self, account_id: str) -> str:
        """The address of the driver's own pages at Stripe: their payouts,
        their bank account."""
        return self._client.v1.accounts.login_links.create(account_id).url

    def transfer(self, *, amount_cts: int, account_id: str, charge_id: str | None, metadata: dict,
                 idempotency_key: str) -> str:
        """Move `amount_cts` of a passenger's payment to the driver's
        account; the transfer's id. `charge_id` ties it to that payment, so
        it can be made before Stripe has released the payment's money."""
        params = {"amount": amount_cts, "currency": CURRENCY, "destination": account_id, "metadata": metadata}
        if charge_id:
            params["source_transaction"] = charge_id
        return self._client.v1.transfers.create(params, {"idempotency_key": idempotency_key}).id

    def event(self, payload: bytes, signature: str | None, secret: str) -> dict:
        """A webhook call from Stripe, its signature checked: {"type",
        "checkout"} (the latter for the events about a Checkout page, else
        None). Raises SignatureError when it isn't Stripe's."""
        event = self._client.construct_event(payload, signature, secret)
        is_checkout = event.type.startswith("checkout.session.")
        return {"type": event.type, "checkout": _checkout(event.data.object) if is_checkout else None}


def _checkout(session) -> dict:
    intent = session.payment_intent
    metadata = session.metadata
    return {
        "id": session.id,
        "status": session.status,
        "paid": session.payment_status == "paid",
        "amount_cts": session.amount_total,
        "payment_intent": intent if isinstance(intent, str) or intent is None else intent.id,
        # The library's own object isn't a mapping.
        "metadata": metadata.to_dict() if hasattr(metadata, "to_dict") else dict(metadata or {}),
    }
