"""
Paying for a seat through the site, with Stripe. See docs/payments.md.

Switched on by two settings, STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET,
and off without them: bookings then work as before (nothing is charged,
passenger and driver settle between themselves), and nothing here can
break the site before the keys exist.

How it goes, when it's on:

  1. A driver accepts a booking (or takes a ride request directly). From
     then on the booking has a payment, 'due': the passenger has until
     pay_by to pay, which is PAY_WITHIN after the acceptance or the pickup
     time, whichever comes first. Paying comes after the driver's answer,
     so nobody pays for a seat they don't get, and a driver who takes a
     ride request books the passenger without them being there.
  2. The passenger pays on Stripe's Checkout page (checkout()): the price
     per seat at that moment, times their seats. Which payment methods the
     page offers (cards, TWINT, Apple Pay, Google Pay...) is set in the
     Stripe dashboard. Stripe tells the site when the payment is made
     (webhook()), and the page the passenger comes back to asks too
     (check()); the driver is told.
  3. A payment still due at pay_by: the booking is cancelled, the seat is
     free again, and both are told (cancel_unpaid()).
  4. A booking that ends before the ride (cancelled by either of them, the
     drive cancelled, an account deleted) ends its payment: nothing is due
     any more, and what was paid is refunded (booking_ended()): all of it,
     except when the passenger cancels late. Until FREE_CANCEL_UNTIL before
     the pickup they get everything back; after that LATE_REFUND_SHARE of
     it, the rest going to the driver; after the pickup time nothing (a
     passenger who doesn't show up has paid). A payment that arrives after
     the booking ended is refunded at once.
  5. When the ride starts, what a passenger paid above the price per seat
     as it is then (it falls when more passengers join) is refunded
     (settle_prices()). Nobody is ever asked for more than they paid.
  6. From the pickup time until the driver is paid, the passenger can say
     that the ride didn't take place (dispute()): the payment is on hold,
     the team is told (a report, reports.py), and decides: refunded to the
     passenger or released to the driver (resolve_dispute(), admin.py).

The site never sees a card number: the passenger types it on Stripe's page.
The money stays in the Stripe account. A payment keeps the trip it was for
and outlives its booking and drive (the record of money that moved).

Refunds are made in two steps, so that none is lost or made twice: first
the amount to give back is written down (refund_due_cts), then Stripe is
told (send_refunds()), with an idempotency key, and again at the next
hourly run if that failed (run(), called by maintenance.py).
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

import bookings
import database as db
import mailer
import notify
import reports
import stripe_gateway
from api import api_error

logger = logging.getLogger("uvicorn.error")

# How long the passenger has to pay after the driver's acceptance, at the
# latest until the pickup time.
PAY_WITHIN = timedelta(hours=24)
# A passenger who cancels gets everything back until this long before the
# pickup, then this share of it (the rest goes to the driver), and nothing
# once the pickup time has passed.
FREE_CANCEL_UNTIL = timedelta(hours=2)
LATE_REFUND_SHARE = 0.5
# Stripe keeps a Checkout page open for 30 minutes at least, 24 hours at most.
CHECKOUT_OPEN_MIN = timedelta(minutes=30)
CHECKOUT_OPEN_MAX = timedelta(hours=23)

# A payment whose booking stands: waiting for the passenger, or made.
OPEN_SQL = "status IN ('due', 'paid') AND ended_at IS NULL"
# How long a payment's record is kept: ten years, as Swiss bookkeeping
# rules ask (the privacy notice says so).
RECORDS_KEPT = timedelta(days=3653)

SETTINGS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
_warned = False
_gateways: dict[str, stripe_gateway.Gateway] = {}


def _setting(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def enabled() -> bool:
    """Whether passengers pay through the site: both settings are there."""
    global _warned
    have = [name for name in SETTINGS if _setting(name)]
    if len(have) == 1 and not _warned:
        # Half a setup must not break the site: payments stay off.
        _warned = True
        missing = next(name for name in SETTINGS if name not in have)
        logger.warning("%s is set but %s isn't: payments are off", have[0], missing)
    return len(have) == len(SETTINGS)


def gateway() -> stripe_gateway.Gateway:
    key = _setting("STRIPE_SECRET_KEY")
    if key not in _gateways:
        _gateways[key] = stripe_gateway.Gateway(key)
    return _gateways[key]


def off_error():
    return api_error(503, "payments_off", "Payments are switched off")


def _not_due():
    return api_error(400, "payment_not_due", "There is nothing to pay for this booking")


def cts(chf: float) -> int:
    return int(round(chf * 100))


def chf(amount_cts: int | None) -> float:
    return round((amount_cts or 0) / 100.0, 2)


def _naive(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------
def get(payment_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    return dict(row) if row else None


def for_booking(booking_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM payments WHERE booking_id = ?", (booking_id,)).fetchone()
    return dict(row) if row else None


def _mine(booking_id: int, user: dict) -> dict:
    """The payment of the user's own booking as a passenger. 404 for a
    booking that isn't theirs, as for one that doesn't exist."""
    payment = for_booking(booking_id)
    if payment is None:
        booking = bookings.find(booking_id)
        if booking is None or booking["passenger_id"] != user["id"]:
            raise api_error(404, "booking_not_found", "Booking not found")
        raise _not_due()
    if payment["passenger_id"] != user["id"]:
        raise api_error(404, "booking_not_found", "Booking not found")
    return payment


def refund_on_cancel_cts(payment: dict, by: str | None, now: datetime) -> int:
    """How much of a paid payment goes back to the passenger when the
    booking is cancelled at `now` by `by` ("passenger", "driver", or None
    when it just went, with its drive): everything, unless the passenger
    cancels late."""
    amount = payment["amount_cts"] or 0
    if by != "passenger":
        return amount
    pickup = _naive(payment["pickup_time"])
    if now <= pickup - FREE_CANCEL_UNTIL:
        return amount
    return int(amount * LATE_REFUND_SHARE) if now < pickup else 0


def _on_hold(payment: dict) -> bool:
    return bool(payment["disputed_at"]) and not payment["dispute_closed_at"]


def _can_dispute(payment: dict, now: datetime) -> bool:
    """Paid, its booking stood, the pickup time has come, the driver isn't
    paid yet, and it wasn't said before."""
    return (
        payment["status"] == "paid" and not payment["ended_at"] and not payment["transfer_id"]
        and not payment["disputed_at"] and _naive(payment["pickup_time"]) <= now
    )


def shown(payment: dict | None, booking: dict, now: datetime | None = None) -> dict | None:
    """What a booking's passenger and driver see of its payment: None when
    it isn't paid through the site."""
    if payment is None:
        return None
    now = now or notify.local_now()
    due = payment["status"] == "due"
    standing = payment["status"] == "paid" and not payment["ended_at"]
    return {
        "status": payment["status"],
        # Still to pay: the price as it is now. Paid: what was paid.
        "amount_chf": booking.get("total_chf") if due else chf(payment["amount_cts"]),
        "pay_by": payment["pay_by"] if due else None,
        "refunded_chf": chf(payment["refund_due_cts"]),
        # If the passenger cancelled now: until when it costs nothing, and
        # what would come back.
        "free_cancel_until": _iso(_naive(payment["pickup_time"]) - FREE_CANCEL_UNTIL) if standing else None,
        "refund_if_cancelled_chf": chf(refund_on_cancel_cts(payment, "passenger", now)) if standing else None,
        "on_hold": _on_hold(payment),
        "can_dispute": _can_dispute(payment, now),
    }


def with_payments(bookings_by_side: dict) -> dict:
    """bookings.for_user()'s answer, each booking with its `payment`."""
    rows = [b for side in bookings_by_side.values() for b in side]
    found = {}
    if rows:
        ids = [b["id"] for b in rows]
        with db.get_conn() as conn:
            for i in range(0, len(ids), 500):  # stay under SQLite's variable limit
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                found.update({
                    r["booking_id"]: dict(r)
                    for r in conn.execute(f"SELECT * FROM payments WHERE booking_id IN ({marks})", chunk)
                })
    for b in rows:
        b["payment"] = shown(found.get(b["id"]), b)
    return bookings_by_side


# ---------------------------------------------------------------------
# Telling people
# ---------------------------------------------------------------------
def _details(payment: dict, **more) -> dict:
    """What a payment's notifications keep: the trip, as the booking's own
    notifications do (the inbox and the emails show it the same way)."""
    return {
        "booking_id": payment["booking_id"],
        "drive": {
            "id": payment["drive_id"], "origin_name": payment["origin_name"],
            "destination_name": payment["destination_name"], "departure_time": payment["pickup_time"],
        },
        **more,
    }


def tell(user_id: int | None, kind: str, payment: dict, **more) -> None:
    user = db.get_user(user_id) if user_id else None
    if user:  # their account may be gone
        notify.send(user["name"], kind, _details(payment, **more))


def _name(user_id: int | None) -> str:
    user = db.get_user(user_id) if user_id else None
    return user["name"] if user else ""


# ---------------------------------------------------------------------
# From the driver's acceptance to the passenger's payment
# ---------------------------------------------------------------------
def booking_accepted(booking: dict | None, now: datetime | None = None) -> None:
    """A driver accepted `booking` (as bookings.view() gives it): from now
    on its passenger has a payment to make. Nothing when payments are off,
    so bookings accepted before they were switched on are never asked for
    one, and nothing for a ride that costs nothing."""
    if not enabled() or booking is None or booking["status"] != "accepted" or not booking.get("total_chf"):
        return
    now = now or notify.local_now()
    pay_by = min(now + PAY_WITHIN, _naive(booking["pickup"]["time"]))
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO payments (
                booking_id, drive_id, passenger_id, driver_id, status, pay_by, seats,
                origin_name, destination_name, pickup_time, dropoff_time, created_at
            ) VALUES (?, ?, ?, ?, 'due', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                booking["id"], booking["drive_id"], booking["passenger_id"], booking["drive"]["driver_id"],
                _iso(pay_by), booking["seats"], booking["origin_name"], booking["destination_name"],
                booking["pickup"]["time"], booking["dropoff"]["time"], db.now_iso(),
            ),
        )


def _language(user: dict) -> str:
    """The user's language when the site has it, else English: one of
    mailer.LANGUAGES itself, never the stored value, which names a folder
    of translations."""
    for known in mailer.LANGUAGES:
        if user.get("language") == known:
            return known
    return "en"


def _line(payment: dict, language: str) -> str:
    """What the Checkout page says is being paid: the trip, its time and
    the seats."""
    when = _naive(payment["pickup_time"]).strftime("%d.%m.%Y %H:%M")
    key = "payment.checkoutLine.one" if payment["seats"] == 1 else "payment.checkoutLine.other"
    return mailer.text(language, key, origin=payment["origin_name"], destination=payment["destination_name"],
                       time=when, count=payment["seats"])


def checkout(booking_id: int, user: dict, base_url: str, now: datetime | None = None) -> str:
    """The address of a Checkout page where `user` pays their booking: the
    price as it is now. A page opened earlier for it is closed first, so
    that a booking can't be paid twice."""
    if not enabled():
        raise off_error()
    now = now or notify.local_now()
    payment = _mine(booking_id, user)
    booking = bookings.find(booking_id)
    if payment["status"] != "due" or booking is None or booking["status"] != "accepted" or not booking["total_chf"]:
        raise _not_due()
    left = _naive(payment["pay_by"]) - now
    if left <= timedelta(0):
        raise api_error(400, "payment_too_late", "The time to pay for this booking is over")
    try:
        if payment["checkout_session_id"] and _close_checkout(payment):
            raise _not_due()  # that page had been paid after all
        open_for = min(max(left, CHECKOUT_OPEN_MIN), CHECKOUT_OPEN_MAX)
        back = f"{base_url.rstrip('/')}/?{{}}={booking_id}#/ride"
        session = gateway().create_checkout(
            amount_cts=cts(booking["total_chf"]), description=_line(payment, _language(user)),
            success_url=back.format("paid"), cancel_url=back.format("unpaid"),
            expires_at=int(time.time() + open_for.total_seconds()),
            metadata={"payment_id": str(payment["id"]), "booking_id": str(booking_id)},
            language=_language(user), email=user.get("email"),
        )
    except stripe_gateway.Error:
        logger.exception("Opening a Checkout page for booking %s failed", booking_id)
        raise api_error(502, "payment_failed", "The payment page couldn't be opened")
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE payments SET checkout_session_id = ? WHERE id = ? AND status = 'due'", (session["id"], payment["id"])
        )
    return session["url"]


def _close_checkout(payment: dict) -> bool:
    """Close the payment's open Checkout page. True if it turns out to have
    been paid (which is then recorded)."""
    session_id = payment["checkout_session_id"]
    try:
        gateway().expire_checkout(session_id)
        return False
    except stripe_gateway.Error:
        # Not open any more: expired by itself, or completed.
        state = gateway().checkout(session_id)
        if not state["paid"]:
            return False
        _apply_checkout(state)
        return True


def _apply_checkout(state: dict) -> None:
    """Record what a Checkout page became. Called for Stripe's webhook
    events, for the passenger coming back from the page, and before a
    payment is given up; the same state can arrive several times."""
    if not state["paid"] or not state["payment_intent"]:
        return
    try:
        payment = get(int(state["metadata"].get("payment_id") or 0))
    except ValueError:
        payment = None
    if payment is None:
        logger.warning("A Checkout page was paid for no known payment: %s", state["id"])
        return
    intent = state["payment_intent"]
    if payment["payment_intent_id"] == intent:
        return  # already recorded
    charge = gateway().charge_of(intent)
    with db.get_conn() as conn:
        made = conn.execute(
            """
            UPDATE payments SET status = 'paid', amount_cts = ?, payment_intent_id = ?, charge_id = ?,
                                checkout_session_id = ?, paid_at = ?
            WHERE id = ? AND status = 'due'
            """,
            (state["amount_cts"], intent, charge, state["id"], db.now_iso(), payment["id"]),
        ).rowcount == 1
        if not made:
            # The booking ended before this payment arrived: it goes back.
            late = conn.execute(
                """
                UPDATE payments SET amount_cts = ?, payment_intent_id = ?, charge_id = ?, checkout_session_id = ?,
                                    paid_at = ?, refund_due_cts = ?
                WHERE id = ? AND status = 'cancelled' AND payment_intent_id IS NULL
                """,
                (state["amount_cts"], intent, charge, state["id"], db.now_iso(), state["amount_cts"], payment["id"]),
            ).rowcount == 1
    if made:
        tell(payment["driver_id"], "payment_received", payment, passenger_name=_name(payment["passenger_id"]))
    elif late:
        send_refunds(payment["id"])
    else:
        # A second payment for a booking already paid: back at once.
        logger.warning("Payment %s was paid twice: refunding %s", payment["id"], intent)
        gateway().refund(intent, state["amount_cts"], f"twice-{intent}")


def check(booking_id: int, user: dict) -> dict:
    """The passenger is back from the Checkout page: ask Stripe what became
    of it, in case its webhook call hasn't arrived yet. Returns the
    payment's status."""
    payment = _mine(booking_id, user)
    if payment["status"] == "due" and payment["checkout_session_id"] and enabled():
        try:
            _apply_checkout(gateway().checkout(payment["checkout_session_id"]))
        except stripe_gateway.Error:
            logger.exception("Asking Stripe about payment %s failed", payment["id"])
        payment = get(payment["id"])
    return {"status": payment["status"]}


def webhook(payload: bytes, signature: str | None) -> dict:
    """A call from Stripe (its signature is checked): a Checkout page was
    paid."""
    if not enabled():
        raise off_error()
    try:
        event = gateway().event(payload, signature, _setting("STRIPE_WEBHOOK_SECRET"))
    except (stripe_gateway.SignatureError, ValueError):
        raise api_error(400, "invalid_signature", "This call isn't signed by Stripe")
    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        _apply_checkout(event["checkout"])
    return {"received": True}


# ---------------------------------------------------------------------
# When a booking ends before the ride
# ---------------------------------------------------------------------
def booking_ended(booking_id: int, by: str | None = None, now: datetime | None = None) -> None:
    """The booking was cancelled or is gone: nothing is due for it any
    more, and what was paid goes back to the passenger, all of it unless
    they cancelled late themselves (`by`: "passenger" or "driver"; see
    refund_on_cancel_cts)."""
    payment = for_booking(booking_id)
    if payment is None or payment["status"] == "cancelled" or payment["ended_at"]:
        return
    now = now or notify.local_now()
    with db.get_conn() as conn:
        given_up = conn.execute(
            "UPDATE payments SET status = 'cancelled', cancelled_at = ? WHERE id = ? AND status = 'due'",
            (db.now_iso(), payment["id"]),
        ).rowcount == 1
        if not given_up:
            conn.execute(
                # MAX: never less than what was already to give back.
                """
                UPDATE payments SET refund_due_cts = MAX(refund_due_cts, ?), ended_at = ?
                WHERE id = ? AND status = 'paid' AND ended_at IS NULL
                """,
                (refund_on_cancel_cts(payment, by, now), db.now_iso(), payment["id"]),
            )
    if not enabled():
        return  # the keys are gone: Stripe is told once they are back (run())
    if given_up:
        if payment["checkout_session_id"]:
            try:
                _close_checkout(payment)  # paid in the meantime: recorded, and refunded
            except stripe_gateway.Error:
                logger.exception("Closing the Checkout page of payment %s failed", payment["id"])
    else:
        send_refunds(payment["id"])


def drive_cancelled(drive_id: int, now: datetime | None = None) -> None:
    """A drive is being cancelled (by its driver, or with their account):
    the payments of the bookings on it end with it. Not those of
    passengers already picked up: a drive deleted after the ride leaves
    what was paid for it as it is."""
    now = now or notify.local_now()
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT booking_id FROM payments WHERE drive_id = ? AND pickup_time > ? AND {OPEN_SQL}",
            (drive_id, _iso(now)),
        ).fetchall()
    for row in rows:
        booking_ended(row["booking_id"], by="driver", now=now)


def account_deleted(user: dict, now: datetime | None = None) -> None:
    """A passenger is deleting their account: the payments of their
    upcoming rides end (their drives' are ended by drive_cancelled())."""
    now = now or notify.local_now()
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT booking_id FROM payments WHERE passenger_id = ? AND pickup_time > ? AND {OPEN_SQL}",
            (user["id"], _iso(now)),
        ).fetchall()
    for row in rows:
        booking_ended(row["booking_id"], by="passenger", now=now)


def send_refunds(payment_id: int | None = None, reason: str | None = None) -> int:
    """Tell Stripe to send back what is due and not sent yet, for one
    payment or for all; the passenger is told (`reason`: why, when the
    notification should say: "shared", the price fell). Returns how many
    refunds."""
    if not enabled():
        return 0
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM payments WHERE refund_due_cts > refunded_cts AND payment_intent_id IS NOT NULL"
            + (" AND id = ?" if payment_id else ""),
            (payment_id,) if payment_id else (),
        )]
    sent = 0
    for payment in rows:
        amount = payment["refund_due_cts"] - payment["refunded_cts"]
        try:
            # The key names this very refund: asked twice, it's made once.
            gateway().refund(
                payment["payment_intent_id"], amount, f"refund-{payment['id']}-{payment['refunded_cts']}-{amount}"
            )
        except stripe_gateway.Error:
            logger.exception("Refunding %s centimes of payment %s failed; it will be tried again", amount, payment["id"])
            continue
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE payments SET refunded_cts = refunded_cts + ?, refunded_at = ? WHERE id = ? AND refunded_cts = ?",
                (amount, db.now_iso(), payment["id"], payment["refunded_cts"]),
            )
        more = {"reason": reason} if reason else {}
        tell(payment["passenger_id"], "payment_refunded", payment, amount_chf=chf(amount), **more)
        sent += 1
    return sent


# ---------------------------------------------------------------------
# The price when the ride starts
# ---------------------------------------------------------------------
def settle_prices(now: datetime | None = None) -> int:
    """For the rides that have started: refund what each passenger paid
    above the price as it is now (it fell if more passengers joined after
    they paid). Once per payment. Returns how many got something back."""
    now = now or notify.local_now()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT * FROM payments
            WHERE status = 'paid' AND ended_at IS NULL AND settled_at IS NULL AND pickup_time <= ?
            """,
            (_iso(now),),
        )]
    refunded = 0
    for payment in rows:
        booking = bookings.find(payment["booking_id"])
        above = 0
        if booking and booking["status"] == "accepted" and booking["total_chf"] is not None:
            above = max(0, payment["amount_cts"] - payment["refund_due_cts"] - cts(booking["total_chf"]))
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE payments SET settled_at = ?, refund_due_cts = refund_due_cts + ? WHERE id = ? AND settled_at IS NULL",
                (db.now_iso(), above, payment["id"]),
            )
        if above:
            send_refunds(payment["id"], reason="shared")
            refunded += 1
    return refunded


# ---------------------------------------------------------------------
# "The ride didn't take place"
# ---------------------------------------------------------------------
def dispute(booking_id: int, user: dict, details: str | None = None, now: datetime | None = None) -> dict:
    """The passenger says the ride they paid for didn't take place: the
    payment is on hold (the driver isn't paid), the driver is told, and
    the team gets a report to decide on. Returns the payment's status."""
    now = now or notify.local_now()
    payment = _mine(booking_id, user)
    if not _can_dispute(payment, now):
        raise api_error(400, "dispute_not_possible", "This can't be reported for this booking any more")
    with db.get_conn() as conn:
        held = conn.execute(
            "UPDATE payments SET disputed_at = ? WHERE id = ? AND disputed_at IS NULL AND transfer_id IS NULL",
            (db.now_iso(), payment["id"]),
        ).rowcount == 1
    if not held:
        raise api_error(400, "dispute_not_possible", "This can't be reported for this booking any more")
    logger.warning("Payment %s is on hold: its passenger says the ride didn't take place", payment["id"])
    try:
        # The team's list of things to look at (admin.py reports).
        text = f"[Payment {payment['id']} is on hold] {(details or '').strip()}".strip()
        reports.file(booking_id, user, "no_show", text)
    except HTTPException:
        # Already reported, or the booking is gone: admin.py payments lists it anyway.
        pass
    tell(payment["driver_id"], "payout_held", payment, passenger_name=user["name"])
    return {"status": "paid", "on_hold": True}


def on_hold() -> list[dict]:
    """The payments waiting for the team's decision, the oldest first."""
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM payments WHERE disputed_at IS NOT NULL AND dispute_closed_at IS NULL ORDER BY disputed_at"
        )]


def resolve_dispute(payment_id: int, refund: bool) -> bool:
    """The team's decision on a payment on hold: `refund` it to the
    passenger in full, or release it to the driver (who is paid at the
    next hourly run). Both are told. False if it isn't on hold."""
    payment = get(payment_id)
    if payment is None or not _on_hold(payment):
        return False
    with db.get_conn() as conn:
        closed = conn.execute(
            f"""
            UPDATE payments SET dispute_closed_at = ?
                {", refund_due_cts = amount_cts, ended_at = COALESCE(ended_at, ?)" if refund else ""}
            WHERE id = ? AND disputed_at IS NOT NULL AND dispute_closed_at IS NULL
            """,
            (db.now_iso(), db.now_iso(), payment_id) if refund else (db.now_iso(), payment_id),
        ).rowcount == 1
    if not closed:
        return False
    if refund:
        send_refunds(payment_id)
        tell(payment["driver_id"], "payout_refunded", payment)
    else:
        tell(payment["passenger_id"], "payment_released", payment)
    return True


def as_text(payment: dict) -> str:
    """A payment on hold in a few lines, for the team (admin.py)."""
    def who(user_id):
        user = db.get_user(user_id) if user_id else None
        return f"{user['name']} <{user['email']}>" if user else "(account deleted)"

    return "\n".join([
        f"Payment #{payment['id']} on hold since {payment['disputed_at'][:16]} UTC: CHF {chf(payment['amount_cts']):.2f}",
        f"  Ride: {payment['origin_name']} → {payment['destination_name']}, pickup {payment['pickup_time']}",
        f"  Passenger: {who(payment['passenger_id'])}; driver: {who(payment['driver_id'])}",
    ])


# ---------------------------------------------------------------------
# Hourly (maintenance.py)
# ---------------------------------------------------------------------
def cancel_unpaid(now: datetime | None = None) -> int:
    """Cancel the bookings whose payment is still due at pay_by: the seat
    is free again, the driver and the passenger are told. Returns how
    many."""
    if not enabled():
        return 0  # nobody can pay: nobody is late
    now = now or notify.local_now()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM payments WHERE status = 'due' AND pay_by <= ?", (_iso(now),)
        )]
    cancelled = 0
    for payment in rows:
        if payment["checkout_session_id"]:
            # Paid without the site having heard of it?
            try:
                _apply_checkout(gateway().checkout(payment["checkout_session_id"]))
            except stripe_gateway.Error:
                logger.exception("Asking Stripe about payment %s failed; it stays due", payment["id"])
                continue
            if get(payment["id"])["status"] != "due":
                continue
        booking = bookings.find(payment["booking_id"])
        if booking and booking["status"] == "accepted":
            bookings.cancel(payment["booking_id"], {"id": booking["passenger_id"]}, reason="unpaid")
            tell(payment["passenger_id"], "booking_unpaid", payment)
            cancelled += 1
        booking_ended(payment["booking_id"])
    return cancelled


def run(now: datetime | None = None) -> dict:
    """The hourly upkeep: cancel what wasn't paid in time, end the payments
    of bookings that ended without this module hearing of it, send the
    refunds that are still due, and delete the records older than
    RECORDS_KEPT. Returns how many of each."""
    now = now or notify.local_now()
    unpaid = cancel_unpaid(now)
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT booking_id FROM payments WHERE pickup_time > ? AND {OPEN_SQL}", (_iso(now),)
        )]
    ended = 0
    for row in rows:
        booking = bookings.find(row["booking_id"])
        if booking is None or booking["status"] != "accepted":
            booking_ended(row["booking_id"], now=now)
            ended += 1
    settled = settle_prices(now)
    refunds = send_refunds()
    with db.get_conn() as conn:
        deleted = conn.execute(
            "DELETE FROM payments WHERE created_at < ? AND status != 'due' AND refund_due_cts <= refunded_cts",
            ((datetime.now(timezone.utc) - RECORDS_KEPT).isoformat(),),
        ).rowcount
    return {
        "unpaid_cancelled": unpaid, "payments_ended": ended, "prices_settled": settled, "refunds_sent": refunds,
        "payments_deleted": deleted,
    }
