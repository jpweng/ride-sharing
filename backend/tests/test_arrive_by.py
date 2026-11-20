"""
A passenger can say when they want to arrive instead of when they want to
leave (time_kind "arrive"): the drop-off is then what is compared with their
time, and it may not be after it. In the matching, through the HTTP API
(search, ride request, a driver taking the request), in what a driver is
told, and on the page. Isolated database, offline.
"""
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import database as db
import matching
import notify
from test_auth import FRIBOURG, TOMORROW, VEVEY, _drive, _sign_in, new_client, outbox  # noqa: F401 (fixtures)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
DAY = (date.today() + timedelta(days=2)).isoformat()

# A drive due north, 100 km in 100 minutes, leaving at 08:00; a passenger
# who gets in after a third of it (08:33) and out after five sixths (09:23).
DRIVE = {
    "id": 1, "driver_name": "Driver", "seats_available": 3, "departure_time": f"{DAY}T08:00",
    "origin_lat": 46.0, "origin_lon": 7.0, "destination_lat": 46.9, "destination_lon": 7.0,
    "route_distance_m": 100_000, "route_duration_s": 6000, "route_geometry": None,
}
START, END = (46.3, 7.0), (46.75, 7.0)


def _match(asked, kind, drive=DRIVE, tolerance=240):
    return matching.match_drive(drive, START, END, f"{DAY}T{asked}", 10, 10, tolerance, time_kind=kind)


def test_the_drop_off_is_compared_with_a_time_to_arrive_by():
    m = _match("09:30", "arrive")
    assert (m["pickup"]["time"][-5:], m["dropoff"]["time"][-5:]) == ("08:33", "09:23")
    assert m["time_diff_min"] == -7  # dropped off 7 minutes before


def test_a_drive_that_gets_there_later_does_not_match():
    assert _match("09:20", "arrive") is None
    assert _match("09:23", "arrive") is not None  # on the minute is in time
    # As a time to leave at, the same times are near the pickup's: a match.
    assert _match("09:20", "depart") is not None


def test_much_earlier_than_asked_is_outside_the_tolerance():
    assert _match("12:00", "arrive", tolerance=120) is None
    assert _match("12:00", "arrive", tolerance=240) is not None


def test_the_latest_arrival_in_time_comes_first():
    later = {**DRIVE, "id": 2, "departure_time": f"{DAY}T08:30"}  # drops off at 09:53
    found = matching.find_matches([DRIVE, later], START, END, f"{DAY}T10:00", 10, 10, 240, time_kind="arrive")
    assert [m["id"] for m in found] == [2, 1]


def test_search_to_arrive_by(new_client, outbox, fake_transit):
    driver = new_client()
    _sign_in(driver, outbox, "d@example.ch", "Driver", "One")
    drive = _drive(driver)  # Vevey -> Fribourg at 18:00, a straight line at 60 km/h
    arrival = datetime.fromisoformat(drive["departure_time"]) + timedelta(seconds=drive["route_duration_s"])
    visitor = new_client()
    fake_transit.add_station("Vevey", VEVEY["lat"], VEVEY["lon"])
    fake_transit.add_station("Fribourg", FRIBOURG["lat"], FRIBOURG["lon"])

    def search(when, kind="arrive"):
        body = {"origin": VEVEY, "destination": FRIBOURG, "requested_time": when.isoformat(timespec="minutes"), "time_kind": kind}
        return visitor.post("/api/match", json=body)

    assert [m["id"] for m in search(arrival + timedelta(minutes=20)).json()["driver_matches"]] == [drive["id"]]
    assert search(arrival - timedelta(minutes=10)).json()["driver_matches"] == []
    # The public transport alternative is asked for the same way.
    assert fake_transit.connection_queries[-1][3] is True
    assert search(arrival, kind="sometime").status_code == 422


def test_a_request_to_arrive_by_keeps_when_to_leave(new_client, outbox):
    passenger = new_client()
    _sign_in(passenger, outbox, "p@example.ch", "Paula", "Passenger")
    asked = f"{DAY}T09:02"
    r = passenger.post("/api/requests", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": asked, "time_kind": "arrive"})
    assert r.status_code == 200, r.json()
    request = r.json()["request"]
    assert request["time_kind"] == "arrive"
    leave = datetime.fromisoformat(request["leave_by"])
    minutes = matching.route_with_fallback([(VEVEY["lat"], VEVEY["lon"]), (FRIBOURG["lat"], FRIBOURG["lon"])])["duration_s"] / 60
    # The trip's duration before, down to five minutes: never later.
    assert leave.minute % 5 == 0
    assert 0 <= (datetime.fromisoformat(asked) - leave).total_seconds() / 60 - minutes < 6
    # A time to leave at has none.
    plain = passenger.post("/api/requests", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{DAY}T17:00"})
    assert (plain.json()["request"]["time_kind"], plain.json()["request"]["leave_by"]) == ("depart", None)


def test_arriving_sooner_than_the_drive_takes_is_refused(new_client, outbox):
    passenger = new_client()
    _sign_in(passenger, outbox, "p@example.ch", "Paula", "Passenger")
    soon = (notify.local_now() + timedelta(minutes=25)).isoformat(timespec="minutes")  # ahead enough, but the drive takes longer
    r = passenger.post("/api/requests", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": soon, "time_kind": "arrive"})
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "arrival_too_soon")


def test_a_driver_who_takes_the_request_leaves_in_time(new_client, outbox):
    passenger, driver = new_client(), new_client()
    _sign_in(passenger, outbox, "p@example.ch", "Paula", "Passenger")
    _sign_in(driver, outbox, "d@example.ch", "Driver", "One")
    asked = f"{DAY}T09:02"
    request = passenger.post("/api/requests", json={
        "origin": VEVEY, "destination": FRIBOURG, "requested_time": asked, "time_kind": "arrive",
    }).json()["request"]
    r = driver.post(f"/api/requests/{request['id']}/accept", json={})
    assert r.status_code == 200, r.json()
    [drive] = driver.get("/api/drives?mine=true").json()
    assert drive["departure_time"] == request["leave_by"]
    arrival = datetime.fromisoformat(drive["departure_time"]) + timedelta(seconds=drive["route_duration_s"])
    assert arrival <= datetime.fromisoformat(asked)


def test_a_driver_is_told_a_departure_that_gets_there_in_time():
    candidate = {
        "trip": {"origin_name": "A", "origin_lat": 46.0, "origin_lon": 7.0, "destination_name": "B",
                 "destination_lat": 46.9, "destination_lon": 7.0, "time": datetime(2030, 1, 1, 8, 0).time()},
        "match": {"departure_time": f"{DAY}T08:00", "pickup": {"time": f"{DAY}T08:33"}, "dropoff": {"time": f"{DAY}T09:23"}},
        "times": 3,
    }
    arrive = notify._trip_details(candidate, {"requested_time": f"{DAY}T10:00", "time_kind": "arrive"})
    assert arrive["suggested_departure"] == f"{DAY}T08:35"  # 83 minutes before, down to five: the drop-off by 10:00
    leave = notify._trip_details(candidate, {"requested_time": f"{DAY}T10:00"})
    assert leave["suggested_departure"] == f"{DAY}T09:25"  # the pickup at about 10:00


def test_rows_made_before_are_times_to_leave_at(isolated_db):
    with sqlite3.connect(db.DB_PATH) as conn:
        for table in ("ride_requests", "open_requests"):
            columns = {row[1]: row[4] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert columns["time_kind"] == "'depart'", table
        assert "leave_by" in {row[1] for row in conn.execute("PRAGMA table_info(open_requests)")}


def test_the_page_asks_leave_at_or_arrive_by():
    html = (FRONTEND / "index.html").read_text()
    select = re.search(r'<select id="ride-time-kind".*?</select>', html, re.S).group(0)
    assert re.findall(r'<option value="(\w+)"', select) == ["depart", "arrive"]
    assert 'time_kind: document.getElementById("ride-time-kind").value' in (FRONTEND / "app.js").read_text()
