"""
Hourly upkeep of the database, run by main.py at startup and then every hour:

  1. Refresh the rolling week of fictional demo drives (demo_drives.py).
  2. Delete visitors' data once it's RETENTION old: drives a month after
     their departure, searches a month after they were made, then names
     with no drive or search left. Nothing in the UI shows any of it by
     then; a visitor's name is recreated the next time they post or search.

Deleted rows also live on in the server's daily disk snapshots, which are
kept for 7 days (infra/server.tf).

Run it by hand with the backend on PYTHONPATH, e.g.:
    PYTHONPATH=backend uv run python backend/maintenance.py
"""
from datetime import datetime, timedelta, timezone

import database as db
import demo_drives

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
        "users": db.delete_inactive_users(),
    }


def run(now: datetime | None = None) -> dict:
    """Refresh demo drives, then purge old visitor data; returns what changed."""
    now = now or datetime.now(demo_drives.LOCAL_TZ).replace(tzinfo=None)
    deleted, created = demo_drives.refresh(now)
    return {"demo_deleted": deleted, "demo_created": created, **purge_old_data(now)}


if __name__ == "__main__":
    print(run())
