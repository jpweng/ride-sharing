"""
Hourly upkeep of the database, run by main.py at startup and then every hour:

  1. Refresh the rolling week of fictional demo drives (demo_drives.py).
     With DEMO_DRIVES=off, delete them all instead, then the trips' routes
     no demo drive uses any more.
  2. Give unanswered ride requests their one extra round of drivers
     (notify.run_extra_rounds), and ask both sides of the rides that are
     over how they went (ratings.ask_after_rides). When seats are paid
     through the site: cancel the bookings not paid in time and send the
     refunds still due (payments.run).
  3. Delete visitors' data once it's RETENTION old: drives and ride
     requests a month after their time, searches and notifications a month
     after they were made. Nothing in the UI shows any of it by then.
     Accounts (signed in by email) stay until their owner deletes them;
     names without an account (demo drivers, and the typed names from before
     sign-in existed) are deleted once nothing references them.
  4. Delete sign-in links a day after they were asked for, and expired
     sessions (see auth.py).

Users from before sign-in by email (a typed name, no email) can't sign in
any more: their drives, searches and requests expire under step 3, then
their names.

Deleted rows also live on in the server's daily disk snapshots, which are
kept for 7 days (infra/server.tf).

Run it by hand with the backend on PYTHONPATH, e.g.:
    PYTHONPATH=backend uv run python backend/maintenance.py
"""
from datetime import datetime, timedelta, timezone

import auth
import database as db
import demo_drives
import notify
import payments
import ratings

RETENTION = timedelta(days=30)


def purge_old_data(now: datetime) -> dict:
    """Delete visitor data older than RETENTION; `now` is naive Swiss local time."""
    cutoff = now - RETENTION
    # departure_time is naive local time, like the frontend sends it;
    # created_at is UTC.
    cutoff_utc = cutoff.replace(tzinfo=demo_drives.LOCAL_TZ).astimezone(timezone.utc)
    return {
        "drives": db.delete_visitor_drives_before(cutoff.isoformat(timespec="minutes")),
        "searches": db.delete_ride_requests_before(cutoff_utc.isoformat()),
        "ride_requests": db.delete_open_requests_before(cutoff.isoformat(timespec="minutes")),
        "notifications": db.delete_notifications_before(cutoff_utc.isoformat()),
        "users": db.delete_inactive_users(),
        "sign_in_links": db.delete_sign_in_links_before(
            (datetime.now(timezone.utc) - auth.SIGN_IN_LINK_RETENTION).isoformat()
        ),
        "sessions": db.delete_expired_sessions(datetime.now(timezone.utc).isoformat()),
    }


def run(now: datetime | None = None) -> dict:
    """Refresh demo drives, run the requests' extra rounds, ask for ratings
    of the rides that are over, then purge old visitor data; returns what
    changed."""
    now = now or datetime.now(demo_drives.LOCAL_TZ).replace(tzinfo=None)
    deleted, created = demo_drives.refresh(now)
    # Off: their routes too. On, they're kept, so the routing server isn't
    # asked again for a trip that comes back.
    routes_deleted = 0 if demo_drives.enabled() else db.delete_unused_demo_routes()
    told = notify.run_extra_rounds(now)
    asked = ratings.ask_after_rides(now)
    return {
        "demo_deleted": deleted, "demo_created": created, "demo_routes_deleted": routes_deleted,
        "extra_round_told": told, "rides_to_rate": asked, **payments.run(now), **purge_old_data(now),
    }


if __name__ == "__main__":
    print(run())
