"""
Paying drivers what their passengers paid through the site (payments.py),
with Stripe Connect. See docs/payments.md. On when payments are.

  - A driver sets up payouts once, on Stripe's own pages (onboard()): their
    identity and their bank account. The site keeps only the identifier of
    that account at Stripe and whether it is ready; it never sees the
    identity document or the IBAN. Drivers can post drives and accept
    passengers without it: the money waits.
  - PAY_OUT_AFTER after a ride's drop-off, what its passenger paid, less
    what was refunded and the service fee, is moved to the driver's account
    at Stripe
    (pay_out(), hourly), and the driver is told. The wait leaves time to
    hear that a ride didn't take place: a payment its passenger put on
    hold (payments.dispute) isn't sent until the team has decided. Stripe
    then pays the account's balance out to the bank account once a week.
  - While a driver with money waiting has no account that is ready, they
    are reminded to set payouts up, at most every REMIND_EVERY.
  - Money that still couldn't be sent UNCLAIMED_AFTER after the ride, or
    whose driver deleted their account, goes back to the passenger: the
    site doesn't keep it.

Like refunds, a transfer is asked of Stripe with an idempotency key naming
the payment: asked twice, it is made once.
"""
import logging
from datetime import datetime, timedelta, timezone

import database as db
import notify
import payments
import stripe_gateway
from api import api_error

logger = logging.getLogger("uvicorn.error")

# After the drop-off, before the money moves to the driver.
PAY_OUT_AFTER = timedelta(hours=24)
# A driver with money waiting and no account ready is reminded this often.
REMIND_EVERY = timedelta(days=7)
# Still not sent this long after the ride: back to the passenger.
UNCLAIMED_AFTER = timedelta(days=60)

# What Stripe's pages are told the account is for.
ACCOUNT_DESCRIPTION = "Shares the costs of car rides with passengers on Ride and Rail"

# A payment that was made, whose money is still in the site's Stripe account.
# What is the driver's of it: what was paid, less refunds and the service fee.
_THEIRS = "amount_cts - refund_due_cts - fee_cts"
_WAITING_SQL = f"status = 'paid' AND transfer_id IS NULL AND {_THEIRS} > 0"
# ...and isn't on hold for the team (payments.dispute).
_TO_SEND_SQL = _WAITING_SQL + " AND (disputed_at IS NULL OR dispute_closed_at IS NOT NULL)"


def enabled() -> bool:
    return payments.enabled()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


# ---------------------------------------------------------------------
# The driver's account at Stripe
# ---------------------------------------------------------------------
def _account(user_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM payout_accounts WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def _sums(user_id: int) -> tuple[int, int]:
    """(centimes paid by the driver's passengers and not sent yet, centimes sent)."""
    with db.get_conn() as conn:
        waiting = conn.execute(
            f"SELECT COALESCE(SUM({_THEIRS}), 0) FROM payments WHERE driver_id = ? AND {_WAITING_SQL}",
            (user_id,),
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT COALESCE(SUM(transferred_cts), 0) FROM payments WHERE driver_id = ?", (user_id,)
        ).fetchone()[0]
    return waiting, sent


def status(user: dict) -> dict:
    """What the Driver page shows: whether payouts are on at all, how far
    the driver's setup is ('none', 'incomplete', 'ready'), what is waiting
    for them and what was sent so far."""
    account = _account(user["id"])
    waiting, sent = _sums(user["id"])
    return {
        "enabled": enabled(),
        "state": "none" if account is None else "ready" if account["ready"] else "incomplete",
        "waiting_chf": payments.chf(waiting),
        "sent_chf": payments.chf(sent),
    }


def _failed(what: str, user: dict):
    logger.exception("%s for user %s failed", what, user["id"])
    return api_error(502, "payout_failed", "Stripe couldn't be reached. Please try again")


def onboard(user: dict, base_url: str) -> str:
    """The address of Stripe's pages where the driver sets payouts up, or
    carries on. Their account at Stripe is opened at the first call."""
    if not enabled():
        raise payments.off_error()
    account = _account(user["id"])
    back = f"{base_url.rstrip('/')}/?payouts={{}}#/drive"
    try:
        if account is None:
            account_id = payments.gateway().create_account(
                email=user.get("email"), description=ACCOUNT_DESCRIPTION, metadata={"user_id": str(user["id"])},
            )
            with db.get_conn() as conn:
                # Two clicks at once: the first one's account is the driver's.
                conn.execute(
                    "INSERT OR IGNORE INTO payout_accounts (user_id, stripe_account_id, created_at) VALUES (?, ?, ?)",
                    (user["id"], account_id, db.now_iso()),
                )
            account = _account(user["id"])
        return payments.gateway().onboarding_link(account["stripe_account_id"], back.format("done"), back.format("again"))
    except stripe_gateway.Error:
        raise _failed("Setting up payouts", user)


def refresh(user: dict) -> dict:
    """Ask Stripe how far the driver's account is (they are back from its
    pages), and answer as status() does."""
    account = _account(user["id"])
    if account is not None and enabled():
        try:
            _check(account)
        except stripe_gateway.Error:
            logger.exception("Asking Stripe about the account of user %s failed", user["id"])
    return status(user)


def _check(account: dict) -> dict:
    """The account with what Stripe says of it now."""
    state = payments.gateway().account(account["stripe_account_id"])
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE payout_accounts SET submitted = ?, ready = ?, checked_at = ? WHERE user_id = ?",
            (int(state["submitted"]), int(state["ready"]), db.now_iso(), account["user_id"]),
        )
    return {**account, "submitted": int(state["submitted"]), "ready": int(state["ready"])}


def dashboard(user: dict) -> str:
    """The address of the driver's own pages at Stripe: what was paid out
    to them, their bank account."""
    if not enabled():
        raise payments.off_error()
    account = _account(user["id"])
    if account is None or not account["submitted"]:
        raise api_error(400, "payouts_not_set_up", "Set up payouts first")
    try:
        return payments.gateway().dashboard_link(account["stripe_account_id"])
    except stripe_gateway.Error:
        raise _failed("Opening the Stripe pages", user)


# ---------------------------------------------------------------------
# Sending the money (hourly, maintenance.py)
# ---------------------------------------------------------------------
def _send(payment: dict, account: dict) -> bool:
    """Move what is left of a payment to its driver's account, once."""
    amount = payment["amount_cts"] - payment["refund_due_cts"] - payment["fee_cts"]
    try:
        transfer_id = payments.gateway().transfer(
            amount_cts=amount, account_id=account["stripe_account_id"], charge_id=payment["charge_id"],
            metadata={"payment_id": str(payment["id"]), "booking_id": str(payment["booking_id"])},
            idempotency_key=f"payout-{payment['id']}",
        )
    except stripe_gateway.Error:
        logger.exception("Sending payment %s to its driver failed; it will be tried again", payment["id"])
        return False
    with db.get_conn() as conn:
        conn.execute(
            """
            UPDATE payments SET transfer_id = ?, transferred_cts = ?, transferred_at = ?
            WHERE id = ? AND transfer_id IS NULL
            """,
            (transfer_id, amount, db.now_iso(), payment["id"]),
        )
    payments.tell(payment["driver_id"], "payout_sent", payment, amount_chf=payments.chf(amount))
    return True


def _give_back(payment: dict) -> None:
    """The money can't reach the driver: it goes back to the passenger."""
    with db.get_conn() as conn:
        conn.execute(
            f"UPDATE payments SET refund_due_cts = amount_cts, ended_at = COALESCE(ended_at, ?) WHERE id = ? AND {_TO_SEND_SQL}",
            (db.now_iso(), payment["id"]),
        )
    payments.send_refunds(payment["id"])
    payments.tell(payment["driver_id"], "payout_lapsed", payment)


def _ready_account(user_id: int | None, checked: dict) -> dict | None:
    """The driver's account if money can be sent to it; Stripe is asked
    once per run about one that wasn't ready yet."""
    if user_id is None:
        return None
    if user_id not in checked:
        account = _account(user_id)
        if account is not None and not account["ready"]:
            try:
                account = _check(account)
            except stripe_gateway.Error:
                logger.exception("Asking Stripe about the account of user %s failed", user_id)
        checked[user_id] = account
    account = checked[user_id]
    return account if account and account["ready"] else None


def pay_out(now: datetime | None = None, driver_id: int | None = None, wait: timedelta = PAY_OUT_AFTER) -> dict:
    """Send drivers what the passengers of their finished rides paid, for
    the rides over since `wait`; `driver_id`: only that driver's. Money
    that couldn't be sent for UNCLAIMED_AFTER, or whose driver is gone,
    goes back to the passenger. Returns how many were {"sent", "given_back"}."""
    counts = {"sent": 0, "given_back": 0}
    if not enabled():
        return counts
    now = now or notify.local_now()
    payments.settle_prices(now)  # first what goes back to passengers because the price fell
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM payments WHERE {_TO_SEND_SQL} AND dropoff_time <= ?"
            + (" AND driver_id = ?" if driver_id else "") + " ORDER BY id",
            (_iso(now - wait), *((driver_id,) if driver_id else ())),
        )]
    checked: dict[int, dict | None] = {}
    for payment in rows:
        account = _ready_account(payment["driver_id"], checked)
        if account:
            counts["sent"] += _send(payment, account)
        elif payment["driver_id"] is None or payment["dropoff_time"] <= _iso(now - UNCLAIMED_AFTER):
            _give_back(payment)
            counts["given_back"] += 1
    return counts


def remind(now: datetime | None = None) -> int:
    """Tell the drivers who have money waiting and no account that is
    ready to set payouts up, at most every REMIND_EVERY. Returns how many."""
    if not enabled():
        return 0
    since = (datetime.now(timezone.utc) - REMIND_EVERY).isoformat()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT p.driver_id, u.name, SUM(p.amount_cts - p.refund_due_cts - p.fee_cts) AS waiting_cts,
                   MIN(p.id) AS payment_id
            FROM payments p JOIN users u ON u.id = p.driver_id
            LEFT JOIN payout_accounts a ON a.user_id = p.driver_id
            WHERE p.status = 'paid' AND p.transfer_id IS NULL AND p.amount_cts - p.refund_due_cts - p.fee_cts > 0
              AND (p.disputed_at IS NULL OR p.dispute_closed_at IS NOT NULL) AND COALESCE(a.ready, 0) = 0
            GROUP BY p.driver_id
            """
        )]
    told = 0
    for row in rows:
        if db.list_notifications(row["name"], kind="payout_setup", since=since):
            continue
        payment = payments.get(row["payment_id"])
        payments.tell(row["driver_id"], "payout_setup", payment, amount_chf=payments.chf(row["waiting_cts"]))
        told += 1
    return told


def run(now: datetime | None = None) -> dict:
    """The hourly upkeep; returns how many payments were sent to drivers,
    how many given back to passengers, and how many drivers reminded."""
    counts = pay_out(now)
    return {"payouts_sent": counts["sent"], "payouts_given_back": counts["given_back"], "payout_reminders": remind(now)}


# ---------------------------------------------------------------------
# When a driver's account goes
# ---------------------------------------------------------------------
def account_deleted(user: dict, now: datetime | None = None) -> None:
    """A driver is deleting their account: what the passengers of their
    finished rides paid is sent to them now, without the usual wait, if
    their payouts are set up (what can't be sent goes back to the
    passengers at the next hourly run). Their account at Stripe stays
    theirs; the site forgets it with the user's row."""
    pay_out(now, driver_id=user["id"], wait=timedelta(0))
