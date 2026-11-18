"""
Tests for maintenance.py: deleting visitors' drives, searches and names a
month after they're last useful, without touching anything newer or the
demo drives. Isolated temp DB and haversine routing -- no network.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import database as db
import demo_drives
import maintenance

NOW = datetime(2026, 10, 21, 12, 0)  # Swiss local time


def _post_drive(driver_name, departure_time, is_demo=False):
    return db.create_drive(
        driver_name=driver_name,
        origin_name="Lausanne", origin_lat=46.5197, origin_lon=6.6323,
        destination_name="Genève", destination_lat=46.2044, destination_lon=6.1432,
        departure_time=departure_time, seats_available=2,
        route_geometry=None, route_distance_m=None, route_duration_s=None,
        route_source="haversine", is_demo=is_demo,
    )


def _search(passenger_name, created_at):
    request_id = db.save_ride_request(
        passenger_name=passenger_name,
        origin_name="Morges", origin_lat=46.5089, origin_lon=6.4979,
        destination_name="Nyon", destination_lat=46.3833, destination_lon=6.2333,
        requested_time="2026-09-01T08:00",
    )
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE ride_requests SET created_at = ? WHERE id = ?", (created_at, request_id))
    return request_id


def _ids(table):
    with sqlite3.connect(db.DB_PATH) as conn:
        return {r[0] for r in conn.execute(f"SELECT id FROM {table}")}


def _user_names():
    with sqlite3.connect(db.DB_PATH) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM users")}


def test_visitor_drives_deleted_a_month_after_departure(isolated_db):
    old = _post_drive("Marie Curie", "2026-09-20T08:00")      # 31 days before NOW
    recent = _post_drive("Marie Curie", "2026-09-22T08:00")   # 29 days before
    upcoming = _post_drive("Marie Curie", "2026-11-01T08:00")
    old_demo = _post_drive("Jean Dupont", "2026-09-01T07:15", is_demo=True)

    assert maintenance.purge_old_data(NOW)["drives"] == 1
    # Departed demo drives are demo_drives.refresh()'s job, not the purge's.
    assert _ids("drives") == {recent, upcoming, old_demo}
    assert old not in _ids("drives")


def test_searches_deleted_a_month_after_they_were_made(isolated_db):
    # NOW is 10:00 UTC (CEST, UTC+2); the cutoff is 2026-09-21T10:00 UTC.
    old = _search("Marie Curie", "2026-09-21T09:59:00+00:00")
    recent = _search("Marie Curie", "2026-09-21T10:01:00+00:00")

    assert maintenance.purge_old_data(NOW)["searches"] == 1
    assert _ids("ride_requests") == {recent}
    assert old not in _ids("ride_requests")


def test_names_deleted_once_nothing_references_them(isolated_db):
    _post_drive("Gone Driver", "2026-09-01T08:00")
    _search("Gone Passenger", "2026-09-01T08:00:00+00:00")
    _post_drive("Active Driver", "2026-11-01T08:00")
    _search("Active Passenger", "2026-10-20T08:00:00+00:00")

    assert maintenance.purge_old_data(NOW)["users"] == 2
    assert _user_names() == {"Active Driver", "Active Passenger"}

    # A purged name is simply recreated on next use.
    _post_drive("Gone Driver", "2026-11-02T08:00")
    assert "Gone Driver" in _user_names()


def test_run_refreshes_demo_drives_and_purges(isolated_db, force_haversine):
    _post_drive("Marie Curie", "2026-09-01T08:00")

    changes = maintenance.run(NOW)

    assert changes["demo_created"] == len(demo_drives._expected(NOW))
    assert changes["drives"] == 1
    assert changes["users"] == 1
    assert maintenance.run(NOW) == {
        "demo_deleted": 0, "demo_created": 0, "extra_round_told": 0,
        "drives": 0, "searches": 0, "ride_requests": 0, "notifications": 0, "users": 0,
        "sign_in_links": 0, "sessions": 0,
    }


def test_accounts_stay_without_data_but_legacy_names_go(isolated_db):
    # A legacy typed name (no email) and an account, neither with any data.
    db.get_or_create_user("Legacy Name")
    db.create_account("marie@example.ch", "~pending", "fr")

    maintenance.purge_old_data(NOW)

    assert _user_names() == {"~pending"}


def test_old_sign_in_links_and_expired_sessions_are_deleted(isolated_db):
    now = datetime.now(timezone.utc)
    db.add_sign_in_link("old", "a@example.ch", "1.2.3.4", "en", now.isoformat())
    db.add_sign_in_link("new", "a@example.ch", "1.2.3.4", "en", now.isoformat())
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE sign_in_links SET created_at = ? WHERE token_hash = 'old'",
                     ((now - timedelta(days=2)).isoformat(),))
    user_id = db.get_or_create_user("Someone")
    db.create_session("expired", user_id, False, (now - timedelta(minutes=1)).isoformat())
    db.create_session("live", user_id, False, (now + timedelta(days=1)).isoformat())

    changes = maintenance.purge_old_data(NOW)

    assert (changes["sign_in_links"], changes["sessions"]) == (1, 1)
    with sqlite3.connect(db.DB_PATH) as conn:
        assert [r[0] for r in conn.execute("SELECT token_hash FROM sign_in_links")] == ["new"]
        assert [r[0] for r in conn.execute("SELECT token_hash FROM sessions")] == ["live"]
    assert "Someone" in _user_names()  # still has a session
