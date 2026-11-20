"""
Notifications by email: what lands in a user's inbox (notify.py,
bookings.py) is also sent to their email address, so they hear of it
without opening the app. See docs/email-notifications.md.

Nothing is sent when the notification is made: main.py calls
send_pending() every minute, which goes through the notifications whose
email is still to decide (notifications.email_state is NULL) and, for each:

  - skips it if emails are switched off (EMAIL_NOTIFICATIONS=off), if email
    can't be sent from here (no sender or site address), if its recipient
    has no email address (demo drivers, names from before sign-in), is
    banned, turned emails off, or has read it in the app meanwhile;
  - holds it during the night (QUIET_FROM to QUIET_UNTIL, Swiss time) when
    it can wait: a passenger's request for a driver, a drive posted for a
    request, the question how a ride went. News about a booking or a
    cancelled drive goes out at any hour: someone is counting on that ride;
  - sends it, at most MAX_ATTEMPTS times, and gives up on notifications
    older than MAX_AGE.

Each email says what happened in one sentence, links to the inbox, and
ends with a link to a page that turns these emails off without signing in
(unsubscribe, a random token per user; the page asks first, so a mail
scanner opening the link changes nothing). The texts are the "email.notify.*"
keys of the dictionaries, in the recipient's language.
"""
import logging
import os
import secrets
from datetime import datetime, time, timedelta, timezone
from html import escape

import database as db
import mailer
import notify

logger = logging.getLogger("uvicorn.error")

SWITCH_ENV = "EMAIL_NOTIFICATIONS"

# No emails that can wait between these hours, Swiss time.
QUIET_FROM = time(22, 0)
QUIET_UNTIL = time(7, 0)
# Kinds that can wait until the morning.
CAN_WAIT = {"request_for_driver", "drive_for_passenger", "rate_ride"}

MAX_AGE = timedelta(hours=24)
MAX_ATTEMPTS = 3
BATCH = 50  # per run, so a backlog can't hold the app up


def switched_on() -> bool:
    """EMAIL_NOTIFICATIONS: "on" (the default, also when empty) or "off"."""
    return (os.environ.get(SWITCH_ENV) or "on").strip().lower() not in ("off", "false", "0", "no")


def _base_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def can_send() -> bool:
    """Whether email can leave from here: always with the console backend
    (development, tests); with SES, once the sender and the site's address
    are set."""
    if (os.environ.get("EMAIL_BACKEND") or "console") == "console":
        return True
    return bool(os.environ.get("EMAIL_FROM", "").strip() and _base_url())


def is_quiet(now: datetime) -> bool:
    """`now`: naive Swiss local time."""
    return now.time() >= QUIET_FROM or now.time() < QUIET_UNTIL


# ---------------------------------------------------------------------
# Each user's switch
# ---------------------------------------------------------------------
def enabled_for(user_id: int) -> bool:
    with db.get_conn() as conn:
        row = conn.execute("SELECT notify_email FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["notify_email"])


def set_enabled(user_id: int, enabled: bool) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET notify_email = ? WHERE id = ?", (int(enabled), user_id))


def _token(user_id: int) -> str:
    """The user's token for the emails' "turn these off" link, made the
    first time it's needed."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT email_token FROM users WHERE id = ?", (user_id,)).fetchone()
        if row["email_token"]:
            return row["email_token"]
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE users SET email_token = ? WHERE id = ?", (token, user_id))
        return token


def user_for_token(token: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email_token = ?", (token or "",)).fetchone()
        return dict(row) if row else None


def unsubscribe(token: str) -> bool:
    """Turn emails off for the user with this token; whether there is one."""
    user = user_for_token(token)
    if user:
        set_enabled(user["id"], False)
    return bool(user)


# ---------------------------------------------------------------------
# What an email says
# ---------------------------------------------------------------------
def _when(iso: str | None) -> str:
    """"20.11.2026 07:30": the Swiss way, the same in every language."""
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return ""


def _trip(origin: str | None, destination: str | None, when: str | None) -> str:
    route = f"{origin} → {destination}" if origin and destination else ""
    return ", ".join(part for part in (route, _when(when)) if part)


def _sentence(n: dict, language: str) -> tuple[str, str]:
    """(what happened, in one sentence; the trip and time it's about)."""
    def t(key, **params):
        return mailer.text(language, f"email.notify.{key}", **params)

    details = n["details"] or {}
    drive = details.get("drive") or {}
    request = db.get_open_request(n["request_id"]) or {}
    request_trip = _trip(request.get("origin_name"), request.get("destination_name"), request.get("requested_time"))
    if request.get("time_kind") == "arrive" and request.get("origin_name"):
        # The request's time is when the passenger wants to be there.
        request_trip = ", ".join((f"{request['origin_name']} → {request['destination_name']}",
                                  t("arriveBy", time=_when(request.get("requested_time")))))
    drive_trip = _trip(drive.get("origin_name"), drive.get("destination_name"), drive.get("departure_time"))
    kind = n["kind"]
    if kind == "request_for_driver":
        return t("requestForDriver", name=request.get("passenger_name", "")), request_trip
    if kind == "drive_for_passenger":
        return t("driveForPassenger"), request_trip
    if kind == "drive_cancelled":
        return t("driveCancelled"), drive_trip
    if kind == "booking_requested":
        return t("bookingRequested", name=details.get("passenger_name", "")), drive_trip
    if kind == "booking_accepted":
        return t("bookingAccepted", name=drive.get("driver_name", "")), drive_trip
    if kind == "booking_declined":
        return t("bookingDeclined", name=drive.get("driver_name", "")), drive_trip
    if kind == "booking_cancelled":
        if details.get("by") == "driver":
            return t("bookingCancelledByDriver", name=drive.get("driver_name", "")), drive_trip
        return t("bookingCancelledByPassenger", name=details.get("passenger_name", "")), drive_trip
    if kind == "booking_drive_cancelled":
        return t("bookingDriveCancelled", name=drive.get("driver_name", "")), drive_trip
    if kind == "request_accepted":
        return t("requestAccepted", name=drive.get("driver_name", "")), drive_trip
    if kind == "rate_ride":
        name = details.get("rated_name", "")
        asked = t("rateDriver", name=name) if details.get("rated_role") == "driver" else t("ratePassenger", name=name)
        return asked, drive_trip
    # A kind this module doesn't know yet: still worth a look at the inbox.
    return t("other"), drive_trip or request_trip


def compose(n: dict, user: dict) -> tuple[str, str, str]:
    """(subject, plain text, HTML) of a notification's email to its recipient."""
    language = user["language"] if user["language"] in mailer.LANGUAGES else "en"

    def t(key, **params):
        return mailer.text(language, f"email.notify.{key}", **params)

    sentence, trip = _sentence(n, language)
    base = _base_url() or "http://localhost:8000"
    inbox = f"{base}/?lang={language}#/inbox"
    off = f"{base}/unsubscribe/{_token(user['id'])}"
    greeting = mailer.text(language, "email.signIn.greeting")
    signature = mailer.text(language, "email.signIn.signature")
    lines = [greeting, sentence, *([trip] if trip else []), t("open")]
    plain = "\n\n".join([*lines, inbox, signature, f"{t('footer')}\n{t('footerOff')}: {off}"]) + "\n"
    html = (
        f'<!DOCTYPE html><html lang="{escape(language)}"><body style="font-family: sans-serif; line-height: 1.5">'
        + f"<p>{escape(greeting)}</p><p><strong>{escape(sentence)}</strong>"
        + (f"<br>{escape(trip)}" if trip else "") + "</p>"
        + f'<p><a href="{escape(inbox)}" style="display: inline-block; padding: 10px 18px; background: #0f6e5f;'
        f' color: #ffffff; text-decoration: none; border-radius: 6px">{escape(t("button"))}</a></p>'
        + f"<p>{escape(signature)}</p>"
        + f'<p style="font-size: 12px; color: #555">{escape(t("footer"))} '
        f'<a href="{escape(off)}">{escape(t("footerOff"))}</a></p>'
        + "</body></html>"
    )
    return sentence, plain, html


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------
def _set_state(notification_id: int, state: str | None, attempted: bool = False) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE notifications SET email_state = ?, email_attempts = email_attempts + ? WHERE id = ?",
            (state, int(attempted), notification_id),
        )


def send_pending(now: datetime | None = None) -> dict:
    """Go through the notifications whose email is still to decide; returns
    how many were {"sent", "skipped", "failed", "held"}. `now`: naive Swiss
    local time."""
    now = now or notify.local_now()
    counts = {"sent": 0, "skipped": 0, "failed": 0, "held": 0}
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id FROM notifications WHERE email_state IS NULL ORDER BY id LIMIT ?", (BATCH,)
        )]
    if not rows:
        return counts
    sending = switched_on() and can_send()
    oldest = (datetime.now(timezone.utc) - MAX_AGE).isoformat()
    for row in rows:
        n = db.get_notification(row["id"])
        if n is None:
            continue
        user = db.get_user(n["recipient_id"])
        if (
            not sending or n["created_at"] < oldest or n["read_at"]
            or not user or not user["email"] or user["is_demo"] or user["banned_at"] or not user["notify_email"]
        ):
            _set_state(n["id"], "skipped")
            counts["skipped"] += 1
            continue
        if n["kind"] in CAN_WAIT and is_quiet(now):
            counts["held"] += 1
            continue
        try:
            mailer.backend().send(user["email"], *compose(n, user))
        except Exception:  # whatever went wrong, the app carries on
            logger.exception("Sending the email of notification %s failed", n["id"])
            failed = n["email_attempts"] + 1 >= MAX_ATTEMPTS
            _set_state(n["id"], "failed" if failed else None, attempted=True)
            counts["failed"] += 1
            continue
        _set_state(n["id"], "sent", attempted=True)
        counts["sent"] += 1
    return counts
