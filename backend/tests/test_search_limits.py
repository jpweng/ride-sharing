"""
What a search does beyond its usual limits: it looks further away when no
drive that can be booked passes near (search.matches_for_search), and it
proposes public transport to the pickup and from the drop-off where that
makes sense (transit_legs.py). Through the /api/match handler, on straight
routes (force_haversine) and a fake timetable (fake_transit).
"""
from datetime import date, datetime, timedelta

import pytest

import database as db
import search
import transit
import transit_legs
from routes import bookings as booking_routes
from routes import drives as drive_routes
from routes import match as match_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
BERN = {"name": "Bern", "lat": 46.9480, "lon": 7.4474}
# From Lausanne, as the crow flies: ~2 km, ~5 km, ~19 km, ~34 km.
OUCHY = {"name": "Ouchy", "lat": 46.5070, "lon": 6.6260}
RENENS = {"name": "Renens", "lat": 46.5370, "lon": 6.5880}
AUBONNE = {"name": "Aubonne", "lat": 46.4953, "lon": 6.3917}
NYON = {"name": "Nyon", "lat": 46.3833, "lon": 6.2396}
# ~7 km past Bern.
BELP = {"name": "Belp", "lat": 46.8911, "lon": 7.4983}


@pytest.fixture
def drivers(user_named, force_haversine, fake_transit):
    return {name.split()[0].lower(): user_named(name) for name in ("Jean D.", "Marie M.", "Paul P.")}


def _drive(driver, origin=LAUSANNE, destination=BERN, at="08:00"):
    return drive_routes.post_drive(drive_routes.DriveCreate(
        origin=origin, destination=destination, departure_time=f"{TOMORROW}T{at}", seats_available=3,
    ), driver)


def _demo_drive(origin=LAUSANNE, destination=BERN, at="08:00"):
    return db.create_drive(
        "Demo Dora", origin["name"], origin["lat"], origin["lon"],
        destination["name"], destination["lat"], destination["lon"],
        f"{TOMORROW}T{at}", 3, route_geometry=None, route_distance_m=None, route_duration_s=None,
        route_source="haversine", is_demo=True,
    )


def _search(origin, destination=BERN, at="08:00"):
    return match_routes.match_ride(match_routes.MatchRequest(
        origin=origin, destination=destination, requested_time=f"{TOMORROW}T{at}",
    ))


def _at(clock: str) -> str:
    return f"{TOMORROW}T{clock}:00+0100"


def _connection(departure: str, arrival: str) -> dict:
    return {"departure": _at(departure), "arrival": _at(arrival), "duration": "00d00:20:00", "transfers": 0,
            "products": ["S"]}


# --- Looking further away ---------------------------------------------------------

def test_a_drive_passing_near_is_found_as_before(drivers):
    _drive(drivers["jean"])
    result = _search(RENENS)

    [match] = result["driver_matches"]
    assert match["wider"] is False and match["pickup"]["gap_km"] < search.SEARCH_RADIUS_KM
    assert (result["search_radius_km"], result["wider_radius_km"]) == (10.0, 30.0)


def test_nothing_near_so_the_search_looks_further(drivers):
    _drive(drivers["jean"])

    [match] = _search(AUBONNE)["driver_matches"]

    assert match["wider"] is True and match["bookable"] is True
    assert search.SEARCH_RADIUS_KM < match["pickup"]["gap_km"] <= search.WIDER_RADIUS_KM
    assert match["price_chf"] > 0


def test_further_has_its_limit_too(drivers):
    _drive(drivers["jean"])
    assert _search(NYON)["driver_matches"] == []


def test_a_bookable_drive_near_means_no_looking_further(drivers):
    _drive(drivers["jean"])                 # from Lausanne: ~19 km from Aubonne
    _drive(drivers["marie"], origin=AUBONNE)  # from Aubonne itself

    matches = _search(AUBONNE)["driver_matches"]

    assert [(m["driver_name"], m["wider"]) for m in matches] == [("Marie M.", False)]


def test_demo_drives_near_dont_stop_the_search_from_looking_further(drivers):
    _demo_drive(origin=AUBONNE)
    _demo_drive()             # a fictional drive from further away stays out
    _drive(drivers["jean"])

    matches = _search(AUBONNE)["driver_matches"]

    # One list, the best first: the demo drive from the door, then the real
    # one from Lausanne.
    assert [(m["driver_name"], m["wider"], m["bookable"]) for m in matches] == [
        ("Demo Dora", False, False), ("Jean D.", True, True),
    ]


def test_a_drive_found_further_away_can_be_booked(drivers):
    drive = _drive(drivers["jean"])
    [match] = _search(AUBONNE)["driver_matches"]
    assert match["wider"] is True

    booking = booking_routes.request_booking(
        drive["id"], booking_routes.BookingCreate(origin=AUBONNE, destination=BERN), drivers["marie"],
    )

    assert booking["status"] == "requested"
    assert booking["pickup"]["gap_km"] == match["pickup"]["gap_km"]


# --- Public transport to the pickup -------------------------------------------------

def test_public_transport_to_a_pickup_too_far_to_walk(drivers, fake_transit):
    _drive(drivers["jean"])
    fake_transit.add_station("Aubonne, gare", AUBONNE["lat"], AUBONNE["lon"])
    fake_transit.add_station("Lausanne", 46.5167, 6.6291)
    fake_transit.add_station("Bern", BERN["lat"], BERN["lon"])
    # Asked to be there by 07:50, ten minutes before the car: the 07:55
    # arrival is too late, leaving at 05:00 is no plan, and of those that
    # fit, the two that leave the least waiting are shown.
    fake_transit.set_connections("Aubonne, gare", "Lausanne", [
        _connection("05:00", "05:40"), _connection("06:10", "06:45"), _connection("06:40", "07:15"),
        _connection("07:10", "07:45"), _connection("07:25", "07:55"),
    ])

    [match] = _search(AUBONNE)["driver_matches"]

    leg = match["first_mile_transit"]
    assert (leg["from_station"], leg["to_station"]) == ("Aubonne, gare", "Lausanne")
    assert [c["arrival"] for c in leg["connections"]] == [_at("07:15"), _at("07:45")]
    assert ("Aubonne, gare", "Lausanne", f"{TOMORROW}T07:50", True) in fake_transit.connection_queries
    # The car goes all the way to Bern: nothing to add after it.
    assert match["last_mile_transit"] is None


def test_no_public_transport_for_a_kilometre_or_two(drivers, fake_transit):
    _drive(drivers["jean"])
    fake_transit.add_station("Lausanne-Ouchy", OUCHY["lat"], OUCHY["lon"])
    fake_transit.add_station("Lausanne", 46.5167, 6.6291)
    fake_transit.add_station("Bern", BERN["lat"], BERN["lon"])
    fake_transit.set_connections("Lausanne-Ouchy", "Lausanne", [_connection("07:30", "07:40")])

    [match] = _search(OUCHY)["driver_matches"]

    assert 1 < match["pickup"]["gap_km"] < transit_legs.MIN_GAP_KM
    assert match["first_mile_transit"] is None
    assert not any(arrive_by for *_, arrive_by in fake_transit.connection_queries)


def test_from_three_kilometres_it_is_proposed(drivers, fake_transit):
    _drive(drivers["jean"])
    fake_transit.add_station("Renens VD", RENENS["lat"], RENENS["lon"])
    fake_transit.add_station("Lausanne", 46.5167, 6.6291)
    fake_transit.add_station("Bern", BERN["lat"], BERN["lon"])
    fake_transit.set_connections("Renens VD", "Lausanne", [_connection("07:35", "07:42")])

    [match] = _search(RENENS)["driver_matches"]

    assert match["wider"] is False and match["pickup"]["gap_km"] >= transit_legs.MIN_GAP_KM
    assert match["first_mile_transit"]["from_station"] == "Renens VD"


@pytest.mark.parametrize("stations,connections", [
    # No stop anywhere near the pickup.
    ([("Aubonne, gare", AUBONNE), ("Bern", BERN)], [_connection("07:10", "07:45")]),
    # No connection that gets there in time.
    ([("Aubonne, gare", AUBONNE), ("Lausanne", LAUSANNE), ("Bern", BERN)], [_connection("07:40", "08:05")]),
    # The timetable has nothing (or can't be reached).
    ([("Aubonne, gare", AUBONNE), ("Lausanne", LAUSANNE), ("Bern", BERN)], []),
])
def test_no_leg_when_it_doesnt_work_out(drivers, fake_transit, stations, connections):
    _drive(drivers["jean"])
    for name, at in stations:
        fake_transit.add_station(name, at["lat"], at["lon"])
    fake_transit.set_connections("Aubonne, gare", "Lausanne", connections)

    [match] = _search(AUBONNE)["driver_matches"]

    assert match["first_mile_transit"] is None


# --- Public transport from the drop-off -----------------------------------------------

def test_public_transport_from_the_drop_off_leaves_after_the_car_arrives(drivers, fake_transit):
    _drive(drivers["jean"])
    [match] = _search(LAUSANNE, destination=BELP)["driver_matches"]
    dropoff = match["dropoff"]
    assert dropoff["gap_km"] >= transit_legs.MIN_GAP_KM
    arrives = datetime.fromisoformat(dropoff["time"])

    def clock(minutes):
        return (arrives + timedelta(minutes=minutes)).strftime("%H:%M")

    fake_transit.add_station("Lausanne", LAUSANNE["lat"], LAUSANNE["lon"])
    fake_transit.add_station("Bern", dropoff["lat"], dropoff["lon"])
    fake_transit.add_station("Belp", BELP["lat"], BELP["lon"])
    # Too tight, fine, and one that would mean waiting half the night.
    fake_transit.set_connections("Bern", "Belp", [
        _connection(clock(4), clock(20)), _connection(clock(15), clock(31)), _connection(clock(300), clock(316)),
    ])

    [match] = _search(LAUSANNE, destination=BELP)["driver_matches"]

    leg = match["last_mile_transit"]
    assert (leg["from_station"], leg["to_station"]) == ("Bern", "Belp")
    assert [c["departure"] for c in leg["connections"]] == [_at(clock(15))]
    assert ("Bern", "Belp", f"{TOMORROW}T{clock(10)}", False) in fake_transit.connection_queries
    assert match["first_mile_transit"] is None


# --- Which matches get them --------------------------------------------------------

def test_only_the_first_few_matches_that_need_a_leg_get_one_bookable_first(monkeypatch):
    looked_up = []
    monkeypatch.setattr(transit_legs, "first_mile", lambda m, station: looked_up.append(m["id"]) or {"leg": m["id"]})
    monkeypatch.setattr(transit_legs, "last_mile", lambda m, station: None)

    def match(match_id, gap_km, bookable=True):
        return {"id": match_id, "bookable": bookable, "pickup": {"gap_km": gap_km}, "dropoff": {"gap_km": 0.0}}

    matches = [match(1, 5.0, bookable=False), match(2, 0.5)] + [match(i, 6.0) for i in range(3, 9)]
    transit_legs.add_to(matches, origin=None, destination=None)

    # The demo drive (1) waits behind the bookable ones; the one at the door
    # (2) needs nothing; five lookups, no more.
    assert looked_up == [3, 4, 5, 6, 7]
    assert [m["first_mile_transit"] for m in matches[:3]] == [None, None, {"leg": 3}]
    assert all(m["last_mile_transit"] is None for m in matches)


def test_arrive_by_is_asked_of_the_timetable(monkeypatch):
    asked = {}

    class Answer:
        def raise_for_status(self):
            pass

        def json(self):
            return {"connections": []}

    def get(url, params=None, timeout=None):
        asked.update(params)
        return Answer()

    monkeypatch.setattr(transit.requests, "get", get)
    transit._cache.clear()

    assert transit.get_connections("Aubonne", "Lausanne", "2026-11-20T07:50", arrive_by=True) == []
    assert (asked["date"], asked["time"], asked["isArrivalTime"]) == ("2026-11-20", "07:50", 1)
    asked.clear()
    transit.get_connections("Aubonne", "Lausanne", "2026-11-20T07:51")
    assert "isArrivalTime" not in asked
