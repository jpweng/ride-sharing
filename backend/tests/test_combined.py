"""
A ride for part of the way and public transport for the rest (combined.py):
where the passenger gets in and out of the car, which trips are proposed,
and the search's answer. Isolated database, offline: the timetable is faked.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import combined
import database as db
import matching
import pytest
from api import LatLon
from routes import match as match_routes

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
DAY = (date.today() + timedelta(days=2)).isoformat()

# A drive due north along longitude 7.0: A (46.0) to B (46.9), 100 km in 100
# minutes, leaving at 08:00. One degree of latitude is about 111 km.
A, B = (46.0, 7.0), (46.9, 7.0)
DRIVE = {
    "id": 1, "driver_name": "Driver", "seats_available": 3, "departure_time": f"{DAY}T08:00",
    "origin_lat": A[0], "origin_lon": A[1], "destination_lat": B[0], "destination_lon": B[1],
    "route_distance_m": 100_000, "route_duration_s": 6000,
}
ROUTE = matching.prepare_route(None, DRIVE)  # two points: the straight line
NEAR_A = (46.05, 7.0)       # 5.6 km up the route
FAR_NORTH = (47.8, 7.0)     # 100 km beyond B
FAR_SOUTH = (45.2, 7.0)     # 89 km before A
NEAR_B = (46.85, 7.0)


def test_ride_first_gets_out_where_the_route_is_closest_to_the_destination():
    part = combined.car_part(DRIVE, ROUTE, NEAR_A, FAR_NORTH, combined.RIDE_FIRST)
    assert part["pickup"]["gap_km"] < 0.1 and part["pickup"]["time"].strftime("%H:%M") == "08:05"
    assert (round(part["dropoff"]["lat"], 2), part["dropoff"]["time"].strftime("%H:%M")) == (46.9, "09:40")
    assert 99 < part["far_gap_km"] < 101 and 93 < part["ride_km"] < 96


def test_transit_first_gets_in_where_the_route_is_closest_to_the_start():
    part = combined.car_part(DRIVE, ROUTE, FAR_SOUTH, NEAR_B, combined.TRANSIT_FIRST)
    assert (round(part["pickup"]["lat"], 2), part["pickup"]["time"].strftime("%H:%M")) == (46.0, "08:00")
    assert part["dropoff"]["gap_km"] < 0.1 and 88 < part["far_gap_km"] < 90


@pytest.mark.parametrize("origin, destination, shape", [
    (NEAR_A, NEAR_B, combined.RIDE_FIRST),         # near both ends: a plain match
    (NEAR_A, NEAR_B, combined.TRANSIT_FIRST),
    (NEAR_B, FAR_SOUTH, combined.RIDE_FIRST),      # the drive goes the other way
    (FAR_NORTH, NEAR_A, combined.TRANSIT_FIRST),
    ((46.05, 7.4), FAR_NORTH, combined.RIDE_FIRST),  # 30 km beside the route: too far to get in
])
def test_no_car_part(origin, destination, shape):
    assert combined.car_part(DRIVE, ROUTE, origin, destination, shape) is None


def test_the_car_must_do_a_real_part_of_the_way():
    # 12 km of a 290 km trip: not worth a change.
    short = {**DRIVE, "destination_lat": 46.11, "route_distance_m": 12_000, "route_duration_s": 720}
    route = matching.prepare_route(None, short)
    far = (48.6, 7.0)
    assert combined.car_part(short, route, A, far, combined.RIDE_FIRST) is None
    assert combined._could_make(short, A, far, combined.RIDE_FIRST, 10.0) is False
    assert combined._could_make(DRIVE, NEAR_A, FAR_NORTH, combined.RIDE_FIRST, 10.0) is True


# --- The search's answer ----------------------------------------------------------
START = LatLon(name="Start", lat=NEAR_A[0], lon=NEAR_A[1])
END = LatLon(name="End", lat=FAR_NORTH[0], lon=FAR_NORTH[1])


def _the_drive(at="08:00", name="Driver", duration_s=6000):
    return db.create_drive(
        driver_name=name, origin_name="A", origin_lat=A[0], origin_lon=A[1],
        destination_name="B", destination_lat=B[0], destination_lon=B[1],
        departure_time=f"{DAY}T{at}", seats_available=3, route_geometry=[[A[1], A[0]], [B[1], B[0]]],
        route_distance_m=100_000, route_duration_s=duration_s, route_source="osrm",
    )


def _connection(leaves, arrives):
    return {"departure": f"{DAY}T{leaves}:00+0100", "arrival": f"{DAY}T{arrives}:00+0100", "duration": None,
            "transfers": 0, "products": ["IC"], "legs": [], "operators": [], "booking_url": None}


@pytest.fixture
def network(isolated_db, fake_transit):
    fake_transit.add_station("B, station", B[0], B[1])
    fake_transit.add_station("End, station", END.lat, END.lon)
    fake_transit.add_station("Start, station", START.lat, START.lon)
    fake_transit.set_connections("B, station", "End, station", [_connection("10:00", "11:00"), _connection("10:30", "11:30")])
    return fake_transit


def test_a_ride_then_public_transport(network):
    drive_id = _the_drive()
    [trip] = combined.find(START, END, f"{DAY}T08:00")
    assert (trip["id"], trip["shape"]) == (drive_id, combined.RIDE_FIRST)
    assert (trip["pickup"]["time"][-5:], trip["dropoff"]["time"][-5:]) == ("08:05", "09:40")
    # The first connection that leaves the stop 10 minutes or more after the car.
    assert trip["transit"]["connection"]["departure"][11:16] == "10:00"
    assert (trip["transit"]["from_station"], trip["transit"]["to_station"]) == ("B, station", "End, station")
    assert trip["change"]["name"] == trip["dropoff"]["name"] == "B, station"
    assert (trip["starts"][-5:], trip["arrives"][-5:], trip["total_min"]) == ("08:05", "11:00", 175)
    assert trip["transit"]["fare"]["full_chf"] > 0


def test_public_transport_then_a_ride(network):
    _the_drive()
    start, end = LatLon(name="Far", lat=FAR_SOUTH[0], lon=FAR_SOUTH[1]), LatLon(name="Near B", lat=NEAR_B[0], lon=NEAR_B[1])
    network.add_station("Far, station", start.lat, start.lon)
    network.add_station("A, station", A[0], A[1])
    network.set_connections("Far, station", "A, station", [_connection("06:10", "07:20"), _connection("06:40", "07:45")])
    [trip] = combined.find(start, end, f"{DAY}T06:30")
    assert trip["shape"] == combined.TRANSIT_FIRST
    # The last connection that is there 10 minutes before the car leaves (08:00).
    assert trip["transit"]["connection"]["departure"][11:16] == "06:40"
    # One lookup, from the time asked for (a trip may start 15 minutes before it).
    assert [q[2:] for q in network.connection_queries] == [(f"{DAY}T06:15", False)]
    assert (trip["starts"][-5:], trip["arrives"][-5:]) == ("06:40", trip["dropoff"]["time"][-5:])
    assert trip["change"]["name"] == trip["pickup"]["name"] == "A, station"


def test_leave_at_does_not_propose_leaving_long_before(network):
    _the_drive()
    assert len(combined.find(START, END, f"{DAY}T08:15")) == 1   # the car is there 10 minutes before
    assert combined.find(START, END, f"{DAY}T09:00") == []      # nearly an hour before: not what was asked


def test_arrive_by_is_about_the_end_of_the_whole_trip(network):
    _the_drive()
    assert len(combined.find(START, END, f"{DAY}T11:15", "arrive")) == 1
    assert combined.find(START, END, f"{DAY}T10:45", "arrive") == []  # public transport gets there at 11:00


def test_arrive_by_takes_the_drive_that_fits_a_connection_in_time(network):
    # The last bus that is there by 11:00 leaves at 08:00; the next arrives at 12:30.
    network.set_connections("B, station", "End, station", [_connection("08:00", "09:00"), _connection("11:30", "12:30")])
    in_time = _the_drive("06:00")                      # at the stop at 07:40
    for at in ("07:00", "07:15", "07:30"):             # closer to 11:00 by the estimate, too late for the bus
        _the_drive(at)
    [trip] = combined.find(START, END, f"{DAY}T11:00", "arrive")
    assert (trip["id"], trip["starts"][-5:], trip["arrives"][-5:]) == (in_time, "06:05", "09:00")
    # One lookup for all the drives that change there: the connections that arrive by the time asked for.
    assert network.connection_queries == [("B, station", "End, station", f"{DAY}T11:00", True)]


def test_arrive_by_a_drive_long_before_those_connections_gets_its_own_lookup(network):
    drive_id = _the_drive("06:00")
    network.set_connections("B, station", "End, station", lambda when: (
        [_connection("09:45", "10:45")] if when.endswith("T11:00") else [_connection("08:00", "09:00")]
    ))
    [trip] = combined.find(START, END, f"{DAY}T11:00", "arrive")
    assert (trip["id"], trip["arrives"][-5:]) == (drive_id, "09:00")
    assert [q[2:] for q in network.connection_queries] == [(f"{DAY}T11:00", True), (f"{DAY}T07:50", False)]


def test_leave_at_a_drive_long_after_the_first_connections_gets_its_own_lookup(network):
    _the_drive("10:00")
    start, end = LatLon(name="Far", lat=FAR_SOUTH[0], lon=FAR_SOUTH[1]), LatLon(name="Near B", lat=NEAR_B[0], lon=NEAR_B[1])
    network.add_station("Far, station", start.lat, start.lon)
    network.add_station("A, station", A[0], A[1])
    network.set_connections("Far, station", "A, station", lambda when: (
        [_connection("06:40", "07:45")] if when.endswith("T06:15") else [_connection("08:30", "09:40")]
    ))
    [trip] = combined.find(start, end, f"{DAY}T06:30")
    assert (trip["shape"], trip["starts"][-5:]) == (combined.TRANSIT_FIRST, "08:30")
    assert [q[2:] for q in network.connection_queries] == [(f"{DAY}T06:15", False), (f"{DAY}T09:50", True)]


def test_routes_are_loaded_for_the_drives_closest_to_when_the_passenger_is_there(network, monkeypatch):
    # To arrive by 11:00 after 195 km, a ride first leaves around 07:45, not around 11:00.
    monkeypatch.setattr(combined, "MAX_ROUTES_LOADED", 1)
    network.set_connections("B, station", "End, station", [_connection("08:00", "09:00")])
    _the_drive("10:30")
    _the_drive("11:00")
    in_time = _the_drive("06:00")
    [trip] = combined.find(START, END, f"{DAY}T11:00", "arrive")
    assert trip["id"] == in_time
    assert combined._away_min(DRIVE, combined.RIDE_FIRST, combined._naive(f"{DAY}T08:30"), "depart", 195.0) == 0
    assert combined._away_min(DRIVE, combined.TRANSIT_FIRST, combined._naive(f"{DAY}T06:30"), "depart", 60.0) == 30


def test_demo_drives_on_the_same_trip_count_as_one_route():
    demo = {**DRIVE, "is_demo": 1}
    assert combined._route_key(demo) == combined._route_key({**demo, "id": 2, "departure_time": f"{DAY}T09:00"})
    assert combined._route_key(DRIVE) != combined._route_key({**DRIVE, "id": 2})


def test_the_change_counts_in_minutes_as_the_times_are_shown(network):
    # The car is at the stop at 09:40:30, shown as 09:40: the 09:50 leaves 10 minutes later.
    _the_drive(duration_s=6030)
    network.set_connections("B, station", "End, station", [_connection("09:50", "10:50")])
    [trip] = combined.find(START, END, f"{DAY}T08:00")
    assert (trip["dropoff"]["time"][-5:], trip["transit"]["connection"]["departure"][11:16]) == ("09:40", "09:50")


def test_without_a_connection_that_fits_there_is_no_trip(network):
    _the_drive()
    network.set_connections("B, station", "End, station", [_connection("12:30", "13:30")])  # nearly three hours' wait
    assert combined.find(START, END, f"{DAY}T08:00") == []
    network.set_connections("B, station", "End, station", [])
    assert combined.find(START, END, f"{DAY}T08:00") == []


def test_much_slower_than_public_transport_alone_is_left_out(network):
    _the_drive()
    alone = {"connections": [_connection("08:10", "09:40")]}   # 90 minutes; the combined trip takes 175
    assert combined.find(START, END, f"{DAY}T08:00", transit_option=alone) == []
    as_quick = {"connections": [_connection("08:10", "10:55")]}
    [trip] = combined.find(START, END, f"{DAY}T08:00", transit_option=as_quick)
    assert trip["vs_transit_min"] == 10


def test_two_drives_at_the_same_time_make_one_proposal(network):
    _the_drive(name="One")
    _the_drive(name="Two")
    assert len(combined.find(START, END, f"{DAY}T08:00")) == 1


def test_a_timetable_that_fails_means_no_combined_trips(network, monkeypatch):
    _the_drive()

    def down(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(combined.transit, "get_connections", down)
    assert combined.find(START, END, f"{DAY}T08:00") == []


def test_the_search_answers_with_combined_trips(network, force_haversine):
    drive_id = _the_drive()
    answer = match_routes.match_ride(match_routes.MatchRequest(
        origin=START.model_dump(), destination=END.model_dump(), requested_time=f"{DAY}T08:00",
    ))
    assert answer["driver_matches"] == []
    [trip] = answer["combined_matches"]
    assert trip["id"] == drive_id and trip["bookable"] is True
    assert trip["price_chf"] is not None           # the car's part, as for a match
    assert trip["driver_name"] is None              # not signed in: who drives isn't said


def test_the_page_shows_them():
    html = (FRONTEND / "index.html").read_text()
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert 'id="ride-combined"' in html and scripts.index("combined.js") > scripts.index("app.js")
    script = (FRONTEND / "combined.js").read_text()
    assert "RESULT_SECTIONS.push(" in script and 'cardExtras("combined", m, card)' in script
    # A card says when the trip is quicker than public transport alone, never that it is longer.
    assert 't("combined.vsFaster"' in script and "vsSlower" not in script and "vsSame" not in script
    # Asking for a seat is for the car's part: to or from the stop where the passenger changes.
    assert 'if (kind === "combined") Object.assign(body, { origin: item.ride_from, destination: item.ride_to });' in (FRONTEND / "bookings.js").read_text()
