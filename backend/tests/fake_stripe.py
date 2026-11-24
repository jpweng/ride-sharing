"""
A fake Stripe for the tests of payments.py and payouts.py, and the fixtures
that switch payments on with it: no network, no keys.
"""
import json
from datetime import date, datetime

import pytest

import notify
import payments
import stripe_gateway

NOW = datetime.fromisoformat(f"{date.today().isoformat()}T12:00")


class FakeStripe:
    """Stands in for stripe_gateway.Gateway, method for method with the
    same parameters (test_stripe_gateway.py checks that): Checkout pages
    that a test pays with pay(), drivers' accounts that it approves with
    approve(), and the refunds and transfers asked for."""

    def __init__(self):
        self.sessions = {}
        self.refunds = []  # (payment intent, centimes), once per idempotency key
        self.refund_keys = set()
        self.failing = set()  # names of the calls that fail
        self.accounts = {}  # drivers' accounts: id -> {"submitted", "ready", "asked"}
        self.links = []  # (account, return address, refresh address)
        self.transfers = []  # (account, centimes, charge), once per idempotency key
        self.transfer_keys = set()

    def _check(self, call):
        if call in self.failing:
            raise stripe_gateway.Error(f"{call} failed")

    def create_checkout(self, *, amount_cts, description, success_url, cancel_url, expires_at, metadata,
                        language=None, email=None):
        self._check("create_checkout")
        asked = {
            "amount_cts": amount_cts, "description": description, "success_url": success_url,
            "cancel_url": cancel_url, "expires_at": expires_at, "metadata": metadata, "language": language,
            "email": email,
        }
        session_id = f"cs_{len(self.sessions) + 1}"
        self.sessions[session_id] = {
            "id": session_id, "status": "open", "paid": False, "amount_cts": asked["amount_cts"],
            "payment_intent": None, "metadata": asked["metadata"], "asked": asked,
        }
        return {"id": session_id, "url": f"https://checkout.test/{session_id}"}

    def checkout(self, session_id):
        self._check("checkout")
        return {k: v for k, v in self.sessions[session_id].items() if k != "asked"}

    def expire_checkout(self, session_id):
        if self.sessions[session_id]["status"] != "open":
            raise stripe_gateway.Error("not open")
        self.sessions[session_id]["status"] = "expired"

    def charge_of(self, payment_intent_id):
        return payment_intent_id.replace("pi_", "ch_")

    def refund(self, payment_intent_id, amount_cts, idempotency_key):
        self._check("refund")
        if idempotency_key not in self.refund_keys:
            self.refund_keys.add(idempotency_key)
            self.refunds.append((payment_intent_id, amount_cts))
        return f"re_{len(self.refunds)}"

    def event(self, payload, signature, secret):
        if signature != f"signed with {secret}":
            raise stripe_gateway.SignatureError("bad signature", signature)
        return json.loads(payload)

    # --- drivers' accounts (payouts.py) ---
    def create_account(self, *, email, description, metadata):
        self._check("create_account")
        account_id = f"acct_{len(self.accounts) + 1}"
        asked = {"email": email, "description": description, "metadata": metadata}
        self.accounts[account_id] = {"submitted": False, "ready": False, "asked": asked}
        return account_id

    def onboarding_link(self, account_id, return_url, refresh_url):
        self.links.append((account_id, return_url, refresh_url))
        return f"https://connect.test/{account_id}/setup"

    def account(self, account_id):
        self._check("account")
        return {k: self.accounts[account_id][k] for k in ("submitted", "ready")}

    def dashboard_link(self, account_id):
        return f"https://connect.test/{account_id}"

    def transfer(self, *, amount_cts, account_id, charge_id, metadata, idempotency_key):
        self._check("transfer")
        if idempotency_key not in self.transfer_keys:
            self.transfer_keys.add(idempotency_key)
            self.transfers.append((account_id, amount_cts, charge_id))
        return f"tr_{len(self.transfers)}"

    def approve(self, account_id):
        """The driver went through Stripe's pages, and Stripe accepted them."""
        self.accounts[account_id].update(submitted=True, ready=True)

    # --- what a passenger does on the page ---
    def pay(self, session_id):
        session = self.sessions[session_id]
        session.update(status="complete", paid=True, payment_intent=session_id.replace("cs_", "pi_"))
        return self.checkout(session_id)

    def last(self):
        return list(self.sessions)[-1]


@pytest.fixture
def stripe(monkeypatch):
    """Payments switched on, with a fake Stripe."""
    fake = FakeStripe()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    monkeypatch.setattr(payments, "gateway", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def at_noon(monkeypatch):
    """It is NOW for the bookings' and payments' rules."""
    monkeypatch.setattr(notify, "local_now", lambda: NOW)
