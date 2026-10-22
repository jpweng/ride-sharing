"""
Tests for demo_drives.refresh(): the rolling week of fictional drives, and
that drives posted by visitors are never touched. Isolated temp DB and
haversine routing -- no network.
"""
import sqlite3
from datetime import datetime

import database as db
import demo_drives

WED_NOON = datetime(2026, 10, 21, 12, 0)
THU_NOON = datetime(2026, 10, 22, 12, 0)


def _all_drives():
    return db.list_drives(upcoming_only=False)


def _post_visitor_drive(driver_name, departure_time):
    return db.create_drive(
        driver_name=driver_name,
        origin_name="Lausanne", origin_lat=46.5197, origin_lon=6.6323,
        destination_name="Genève", destination_lat=46.2044, destination_lon=6.1432,
        departure_time=departure_time, seats_available=2,
        route_geometry=None, route_distance_m=None, route_duration_s=None,
        route_source="haversine",
    )


def test_fills_the_coming_week(isolated_db, force_haversine):
    assert demo_drives.refresh(WED_NOON) == (0, 17)

    drives = _all_drives()
    assert len(drives) == 17
    assert all(d["is_demo"] == 1 for d in drives)
    # Nothing already departed; the morning commutes of Wednesday are skipped.
    assert drives[0]["departure_time"] == "2026-10-21T17:30"
    assert drives[-1]["departure_time"] == "2026-10-27T17:30"
    assert {d["departure_time"] for d in drives if d["driver_name"] == "Luca Bianchi"} == {
        "2026-10-24T09:30", "2026-10-25T18:00",
    }


def test_refresh_is_idempotent(isolated_db, force_haversine):
    demo_drives.refresh(WED_NOON)
    assert demo_drives.refresh(WED_NOON) == (0, 0)
    assert len(_all_drives()) == 17


def test_next_day_drops_departed_and_adds_new_day(isolated_db, force_haversine):
    demo_drives.refresh(WED_NOON)

    # Departed by Thursday noon: Wednesday 17:30, Thursday 07:15 and 07:45.
    # New day (next Wednesday): the three weekday commutes plus Anna's.
    assert demo_drives.refresh(THU_NOON) == (3, 4)

    departures = [d["departure_time"] for d in _all_drives()]
    assert min(departures) == "2026-10-22T17:30"
    assert max(departures) == "2026-10-28T17:30"


def test_visitor_drives_are_never_touched(isolated_db, force_haversine):
    past = _post_visitor_drive("Marie Curie", "2026-10-21T08:00")
    future = _post_visitor_drive("Marie Curie", "2026-11-30T08:00")
    # A visitor using a demo driver's name, at a demo drive's exact time.
    same_as_demo = _post_visitor_drive("Jean Dupont", "2026-10-23T07:15")

    demo_drives.refresh(WED_NOON)
    demo_drives.refresh(THU_NOON)

    visitor = {d["id"] for d in _all_drives() if not d["is_demo"]}
    assert visitor == {past, future, same_as_demo}
    # The demo drive at the same time still exists alongside the visitor's.
    jean_fri = [d for d in _all_drives()
                if d["driver_name"] == "Jean Dupont" and d["departure_time"] == "2026-10-23T07:15"]
    assert sorted(d["is_demo"] for d in jean_fri) == [0, 1]


def test_init_db_adds_is_demo_to_an_existing_database(isolated_db):
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("DROP TABLE drives")
        conn.execute("""
            CREATE TABLE drives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL, driver_name TEXT NOT NULL,
                origin_name TEXT NOT NULL, origin_lat REAL NOT NULL, origin_lon REAL NOT NULL,
                destination_name TEXT NOT NULL,
                destination_lat REAL NOT NULL, destination_lon REAL NOT NULL,
                departure_time TEXT NOT NULL, seats_available INTEGER NOT NULL DEFAULT 1,
                route_geometry TEXT, route_distance_m REAL, route_duration_s REAL,
                route_source TEXT, created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO drives (driver_id, driver_name, origin_name, origin_lat, origin_lon,
                destination_name, destination_lat, destination_lon, departure_time, created_at)
            VALUES (1, 'Old Driver', 'A', 46.5, 6.6, 'B', 46.2, 6.1, '2026-11-01T08:00', 'x')
        """)

    db.init_db()

    [old] = _all_drives()
    assert old["is_demo"] == 0
