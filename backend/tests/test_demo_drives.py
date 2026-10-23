"""
Tests for demo_drives.refresh(): the rolling week of fictional drives, that
drives posted by visitors are never touched, and that the network covers
the corridors and regions it's meant to. Isolated temp DB and haversine
routing -- no network.
"""
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

import database as db
import demo_drives
import matching

WED_NOON = datetime(2026, 10, 21, 12, 0)
THU_NOON = datetime(2026, 10, 22, 12, 0)


def _all_drives():
    return db.list_drives(upcoming_only=False, with_geometry=False)


def _expected_count(now):
    return len(demo_drives._expected(now))


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
    expected = _expected_count(WED_NOON)
    assert expected > 400
    assert demo_drives.refresh(WED_NOON) == (0, expected)

    drives = _all_drives()
    assert len(drives) == expected
    assert all(d["is_demo"] == 1 for d in drives)
    departures = [d["departure_time"] for d in drives]
    # Nothing already departed, nothing beyond the 7-day window.
    assert min(departures) > "2026-10-21T12:00"
    assert max(departures) < "2026-10-28T00:00"


def test_refresh_is_idempotent(isolated_db, force_haversine):
    demo_drives.refresh(WED_NOON)
    assert demo_drives.refresh(WED_NOON) == (0, 0)
    assert len(_all_drives()) == _expected_count(WED_NOON)


def test_next_day_drops_departed_and_adds_new_day(isolated_db, force_haversine):
    demo_drives.refresh(WED_NOON)
    departed = sum(1 for k in demo_drives._expected(WED_NOON) if k[1] <= "2026-10-22T12:00")
    new_day = sum(1 for k in demo_drives._expected(THU_NOON) if k[1] >= "2026-10-28")

    assert demo_drives.refresh(THU_NOON) == (departed, new_day)
    assert len(_all_drives()) == _expected_count(THU_NOON)


def test_removed_template_drives_are_deleted(isolated_db, force_haversine, monkeypatch):
    demo_drives.refresh(WED_NOON)
    gone = demo_drives.DEMO_DRIVES[0]
    monkeypatch.setattr(demo_drives, "DEMO_DRIVES", demo_drives.DEMO_DRIVES[1:])

    deleted, created = demo_drives.refresh(WED_NOON)

    assert created == 0
    assert deleted > 0
    assert not [d for d in _all_drives()
                if d["driver_name"] == gone["driver_name"]
                and d["origin_name"] == gone["origin"]["name"]]


def test_routes_are_reused_from_earlier_demo_drives(isolated_db, monkeypatch):
    calls = []

    def fake_osrm(points):
        calls.append(points)
        return {"distance_m": 1000.0, "duration_s": 60.0, "geometry": [[p[1], p[0]] for p in points]}

    monkeypatch.setattr(matching, "osrm_route", fake_osrm)
    monkeypatch.setattr(demo_drives, "ROUTING_PAUSE_S", 0)

    demo_drives.refresh(WED_NOON)
    trips = {(s["origin"]["name"], s["destination"]["name"]) for s in demo_drives.DEMO_DRIVES}
    assert len(calls) == len(trips)

    # The next day's new drives reuse the stored routes.
    calls.clear()
    demo_drives.refresh(THU_NOON)
    assert calls == []


def test_visitor_drives_are_never_touched(isolated_db, force_haversine):
    past = _post_visitor_drive("Marie Curie", "2026-10-21T08:00")
    future = _post_visitor_drive("Marie Curie", "2026-11-30T08:00")
    # A visitor using a demo driver's name, at a demo drive's exact time.
    same_as_demo = _post_visitor_drive("Jean Dupont", "2026-10-23T07:15")

    demo_drives.refresh(WED_NOON)
    demo_drives.refresh(THU_NOON)

    visitor = {d["id"] for d in _all_drives() if not d["is_demo"]}
    assert visitor == {past, future, same_as_demo}
    jean_fri = [d for d in _all_drives()
                if d["driver_name"] == "Jean Dupont" and d["departure_time"] == "2026-10-23T07:15"]
    assert sorted(d["is_demo"] for d in jean_fri) == [0, 1]


def test_no_driver_has_two_drives_at_once():
    slots = Counter(
        (s["driver_name"], day, s["time"])
        for s in demo_drives.DEMO_DRIVES
        for day in s["weekdays"]
    )
    assert [slot for slot, n in slots.items() if n > 1] == []


def _drives_between(origin, destination, day):
    """Demo drives on `day` that a passenger from `origin` to `destination` would
    be matched with: same direction, route passing within a few km of both."""
    o, d = demo_drives.PLACES[origin], demo_drives.PLACES[destination]
    found = []
    for spec in demo_drives.DEMO_DRIVES:
        if day.weekday() not in spec["weekdays"]:
            continue
        drive = {
            "origin_lat": spec["origin"]["lat"], "origin_lon": spec["origin"]["lon"],
            "destination_lat": spec["destination"]["lat"], "destination_lon": spec["destination"]["lon"],
        }
        if matching.straight_line_detour_km(drive, o, d) <= matching.MAX_DETOUR_KM:
            found.append(datetime.combine(day, spec["time"]))
    return found


def test_vevey_fribourg_covered_all_day():
    # Any weekday search between 07:00 and 19:00 has a drive within the
    # matching time window.
    day = datetime(2026, 10, 23).date()  # a Friday
    departures = _drives_between("Vevey", "Fribourg", day)
    window = timedelta(hours=matching.MAX_TIME_WINDOW_HOURS)
    for hour in range(7, 20):
        asked = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
        assert any(abs(dep - asked) <= window for dep in departures), hour


def test_ticino_and_graubunden_well_covered():
    ticino = {"Airolo", "Biasca", "Bellinzona", "Locarno", "Cevio", "Lugano", "Ponte Tresa",
              "Mendrisio", "Chiasso"}
    graubunden = {"Chur", "Landquart", "Klosters", "Davos", "Arosa", "Flims", "Ilanz",
                  "Disentis/Mustér", "Thusis", "St. Moritz", "Zernez", "Scuol", "Poschiavo", "Roveredo"}
    per_week = Counter()
    for s in demo_drives.DEMO_DRIVES:
        for region, places in (("TI", ticino), ("GR", graubunden)):
            if s["origin"]["name"] in places or s["destination"]["name"] in places:
                per_week[region] += len(s["weekdays"])
    assert per_week["TI"] >= 100
    assert per_week["GR"] >= 120


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
