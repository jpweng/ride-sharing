"""
Reports: a passenger or a driver tells the team about a problem with the
other side of a booking (a no-show, lateness, unsafe driving, behaviour).
See docs/ratings-and-reports.md.

  - Who: the two sides of a booking, about the other one, once per booking;
    whatever the booking's status (a driver who cancels at the last minute
    can be reported too).
  - What is kept: who reported whom, the reason, their words, and the ride
    as it was (the booking and its drive are deleted 30 days after the
    departure; the report stays until it's handled and beyond).
  - Who sees it: only the team. The reported person isn't told. Each new
    report is logged, and emailed to REPORTS_EMAIL when that's set.
  - Handling: `admin.py reports` lists the open ones, `admin.py
    close-report <id> [note]` closes one; banning is `admin.py ban`.
"""
import json
import logging
import os
import sqlite3

import database as db
import mailer
import ratings
from api import api_error

logger = logging.getLogger("uvicorn.error")

REASONS = ("no_show", "late", "unsafe", "behaviour", "other")
MAX_DETAILS_CHARS = 1000


def file(booking_id: int, reporter: dict, reason: str, details: str | None = None) -> dict:
    """`reporter` reports the other side of the booking; returns the report."""
    booking, other_role = ratings.sides(booking_id, reporter)
    if other_role == "driver":
        reported_id, reported_name = booking["driver_id"], booking["driver_name"]
    else:
        reported_id, reported_name = booking["passenger_id"], booking["passenger_name"]
    context = {
        "reported_as": other_role,
        "booking_status": booking["status"],
        "drive": f"{booking['drive_origin_name']} → {booking['drive_destination_name']}",
        "departure_time": booking["departure_time"],
        "pickup_time": booking["pickup_time"],
        "passenger": booking["passenger_name"],
        "driver": booking["driver_name"],
    }
    details = (details or "").strip()[:MAX_DETAILS_CHARS] or None
    try:
        with db.get_conn() as conn:
            report_id = conn.execute(
                """
                INSERT INTO reports (booking_id, reporter_id, reporter_name, reported_id, reported_name,
                                     reason, details, context, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (booking_id, reporter["id"], reporter["name"], reported_id, reported_name,
                 reason, details, json.dumps(context), db.now_iso()),
            ).lastrowid
    except sqlite3.IntegrityError:
        raise api_error(400, "already_reported", "You have already reported this ride")
    report = get(report_id)
    logger.warning("Report #%s filed: %s, about a %s", report_id, reason, other_role)
    _tell_the_team(report)
    return report


def _row(row) -> dict:
    report = dict(row)
    report["context"] = json.loads(report["context"])
    return report


def get(report_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return _row(row) if row else None


def open_reports() -> list[dict]:
    """The reports still to handle, the oldest first."""
    with db.get_conn() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM reports WHERE closed_at IS NULL ORDER BY id")]


def close(report_id: int, note: str | None = None) -> bool:
    """Mark a report handled; False if there's no such open report."""
    with db.get_conn() as conn:
        return conn.execute(
            "UPDATE reports SET closed_at = ?, closing_note = ? WHERE id = ? AND closed_at IS NULL",
            (db.now_iso(), note, report_id),
        ).rowcount == 1


def as_text(report: dict) -> str:
    """A report in a few lines, for the team (the email, admin.py)."""
    c = report["context"]
    return "\n".join([
        f"Report #{report['id']} ({report['created_at'][:16]} UTC): {report['reason']}",
        f"  {report['reporter_name']} reports {report['reported_name']} (the {c['reported_as']})",
        f"  Ride: {c['drive']}, departure {c['departure_time']}, pickup {c['pickup_time']};"
        f" driver {c['driver']}, passenger {c['passenger']}; booking {c['booking_status']}",
        f"  In their words: {report['details'] or '(nothing added)'}",
    ])


def _tell_the_team(report: dict) -> None:
    """Email the report to REPORTS_EMAIL, if set. Never fails the report."""
    to = (os.environ.get("REPORTS_EMAIL") or "").strip()
    if not to:
        return
    try:
        text = as_text(report) + "\n\nList the open reports on the server: python backend/admin.py reports\n"
        mailer.backend().send(to, f"Ride and Rail: report #{report['id']} ({report['reason']})", text,
                              f"<pre>{mailer.escape(text)}</pre>")
    except Exception:
        logger.exception("Emailing report #%s to the team failed", report["id"])
