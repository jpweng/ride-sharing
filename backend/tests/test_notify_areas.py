"""
Ride requests reaching drivers by area (notify.py with areas.py): a driver
with an area the request starts or ends in is told, on top of the drivers
who made the trip before, with the same limits. Isolated database, offline.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import areas
import database as db
import demo_drives
import notify
from routes import ride_requests as request_routes
from routes.match import MatchRequest

VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
MONTREUX = {"name": "Montreux", "lat": 46.4312, "lon": 6.9107}  # ~6 km from Vevey
FRIBOURG = {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619}
BULLE = {"name": "Bulle", "lat": 46.6193, "lon": 7.0569}
SION = {"name": "Sion", "lat": 46.2331, "lon": 7.3606}

# Aigle - Vevey - Bulle - Fribourg - Bern, for drivers who made the trip.
AIGLE_BERN = [
    [6.966930, 46.317162], [6.923530, 46.394032], [6.841900, 46.462800], [6.900000, 46.527000],
    [7.056900, 46.619300], [7.161900, 46.806500], [7.447398, 46.940830],
]

TOMORROW = date.today() + timedelta(days=1)
IN_TWO_DAYS = date.today() + timedelta(days=2)


@pytest.fixture(autouse=True)
def no_demo_drivers(monkeypatch):
    monkeypatch.setattr(demo_drives, "DEMO_DRIVES", [])
    notify._demo_geometries.clear()


@pytest.fixture
def driver(user_named):
    """name, (place, radius_km)... -> a user with those areas."""
    def make(name, *their_areas):
        user = user_named(name)
        for place, radius_km in their_areas:
            assert areas.add(user["id"], place["name"], place["lat"], place["lon"], radius_km)
        return user
    return make


def _request(passenger="Marie M.", origin=VEVEY, destination=FRIBOURG, day=TOMORROW, at="18:30"):
    return db.create_open_request(passenger, origin, destination, f"{day}T{at}", 5, 5, 120)


def _past_drive(name, days_ago, at="18:00"):
    day = date.today() - timedelta(days=days_ago)
    (olon, olat), (dlon, dlat) = AIGLE_BERN[0], AIGLE_BERN[-1]
    return db.create_drive(
        driver_name=name, origin_name="Aigle", origin_lat=olat, origin_lon=olon,
        destination_name="Bern", destination_lat=dlat, destination_lon=dlon,
        departure_time=f"{day}T{at}", seats_available=3, route_geometry=AIGLE_BERN,
        route_distance_m=100_000, route_duration_s=6000, route_source="osrm",
    )


def _details(name):
    return [n["details"] for n in notify.inbox(name)["notifications"]]


# --- Which ends are in an area -----------------------------------------------------

def test_ends_inside_an_area(isolated_db):
    request = _request()
    around = lambda place, km: {**place, "radius_km": km}  # noqa: E731
    assert areas.ends_inside(around(VEVEY, 5), request) == ["origin"]
    assert areas.ends_inside(around(FRIBOURG, 5), request) == ["destination"]
    assert areas.ends_inside(around(MONTREUX, 5), request) == []      # Vevey is ~6 km away
    assert areas.ends_inside(around(MONTREUX, 10), request) == ["origin"]
    assert areas.ends_inside(around(BULLE, 30), request) == ["origin", "destination"]
    assert areas.ends_inside(around(SION, 30), request) == []


# --- Who is told ---------------------------------------------------------------------

def test_a_driver_with_an_area_at_the_start_is_told_without_ever_driving_there(driver):
    driver("Zoe Z.", (VEVEY, 5))
    told = notify.notify_drivers(_request())
    assert told == ["Zoe Z."]
    [details] = _details("Zoe Z.")
    assert details["area"]["name"] == "Vevey" and details["area"]["radius_km"] == 5
    assert details["ends"] == ["origin"]
    assert "trip" not in details


def test_an_area_at_the_destination_counts_too(driver):
    driver("Zoe Z.", (FRIBOURG, 10))
    assert notify.notify_drivers(_request()) == ["Zoe Z."]
    assert _details("Zoe Z.")[0]["ends"] == ["destination"]


def test_an_area_the_request_doesnt_touch(driver):
    driver("Far F.", (SION, 30))
    driver("Near N.", (MONTREUX, 5))  # Vevey is just outside
    assert notify.notify_drivers(_request()) == []


def test_both_ends_before_one_then_the_nearest_to_the_centre(driver):
    driver("Near N.", (MONTREUX, 10))             # start inside, ~6 km from the centre
    driver("Centre C.", (VEVEY, 5))               # start inside, at the centre
    driver("Both B.", (VEVEY, 20), (FRIBOURG, 5))  # start and destination, in two areas
    driver("Wide W.", (BULLE, 30))                # both in one area
    told = notify.notify_drivers(_request(), area_limit=10)
    # Both ends first (the nearer centre first), then one end, nearest first.
    assert told == ["Both B.", "Wide W.", "Centre C.", "Near N."]
    # A driver with an area at each end covers both, like one wide area.
    assert _details("Both B.")[0]["ends"] == ["origin", "destination"]
    assert _details("Both B.")[0]["area"]["name"] in ("Vevey", "Fribourg")
    assert _details("Wide W.")[0]["ends"] == ["origin", "destination"]
    assert _details("Near N.")[0]["ends"] == ["origin"]


def test_drivers_who_fit_equally_well_take_turns(driver):
    # Both picked Vevey itself: the same point, the same distance.
    driver("Anna A.", (VEVEY, 5))
    driver("Zoe Z.", (VEVEY, 5))
    assert notify.notify_drivers(_request(day=TOMORROW), area_limit=1) == ["Anna A."]
    # Next time, the one who hasn't been told yet.
    assert notify.notify_drivers(_request("Paul P.", destination=SION, day=IN_TWO_DAYS), area_limit=1) == ["Zoe Z."]


def test_three_by_area_on_top_of_three_who_made_the_trip(driver, user_named):
    for name in ("Trip A.", "Trip B.", "Trip C.", "Trip D."):
        user_named(name)
        _past_drive(name, 7)
    for i, name in enumerate(("Area A.", "Area B.", "Area C.", "Area D.")):
        driver(name, (VEVEY, 5 * (i + 1)) if i < 3 else (VEVEY, 30))
    told = notify.notify_drivers(_request())
    assert len(told) == notify.DRIVERS_PER_REQUEST + notify.AREA_DRIVERS_PER_REQUEST
    assert [n for n in told if n.startswith("Trip")] == told[:3]
    assert [n for n in told if n.startswith("Area")] == told[3:]


def test_a_driver_found_both_ways_is_told_once_as_one_who_made_the_trip(driver):
    driver("Both W.", (VEVEY, 5))
    _past_drive("Both W.", 7)
    assert notify.notify_drivers(_request()) == ["Both W."]
    [details] = _details("Both W.")
    assert "trip" in details and "area" not in details


def test_who_isnt_told_by_area(driver):
    driver("Marie M.", (VEVEY, 5))          # the passenger themself
    off = driver("Off O.", (VEVEY, 5))      # turned request notifications off
    db.update_user_settings(off["name"], notify_requests=False)
    muted = driver("Muted M.", (VEVEY, 5))  # not interested in this route
    db.add_route_mute(muted["name"], (VEVEY["lat"], VEVEY["lon"]), (FRIBOURG["lat"], FRIBOURG["lon"]))
    banned = driver("Banned B.", (VEVEY, 5))
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET banned_at = ? WHERE id = ?", (db.now_iso(), banned["id"]))
    driver("Zoe Z.", (VEVEY, 10))
    assert notify.notify_drivers(_request()) == ["Zoe Z."]


def test_the_budget_is_the_same_one_a_day(driver):
    driver("Zoe Z.", (VEVEY, 5))
    driver("Next N.", (MONTREUX, 10))
    assert notify.notify_drivers(_request(destination=FRIBOURG), area_limit=1) == ["Zoe Z."]
    # A second request the same day, another route: Zoe has had hers today,
    # so the next driver is tried.
    assert notify.notify_drivers(_request("Paul P.", destination=SION), area_limit=1) == ["Next N."]


def test_a_second_passenger_on_the_same_trip_is_merged(driver):
    driver("Zoe Z.", (VEVEY, 5))
    assert notify.notify_drivers(_request()) == ["Zoe Z."]
    assert notify.notify_drivers(_request("Paul P.", at="18:45")) == ["Zoe Z."]
    [n] = notify.inbox("Zoe Z.")["notifications"]
    assert n["passengers"] == 2


def test_a_driver_who_posted_a_matching_drive_isnt_told(driver):
    zoe = driver("Zoe Z.", (VEVEY, 5))
    (olon, olat), (dlon, dlat) = AIGLE_BERN[0], AIGLE_BERN[-1]
    db.create_drive(
        driver_name=zoe["name"], origin_name="Aigle", origin_lat=olat, origin_lon=olon,
        destination_name="Bern", destination_lat=dlat, destination_lon=dlon,
        departure_time=f"{TOMORROW}T18:00", seats_available=3, route_geometry=AIGLE_BERN,
        route_distance_m=100_000, route_duration_s=6000, route_source="osrm",
    )
    assert notify.notify_drivers(_request()) == []


def test_a_demo_request_reaches_nobody_by_area(driver, monkeypatch):
    driver("Zoe Z.", (VEVEY, 5))
    monkeypatch.setattr(notify, "is_demo_request", lambda request: True)
    assert notify.notify_drivers(_request()) == []


def test_the_extra_round_tells_two_more_of_either_kind(driver, user_named):
    for name in ("Area A.", "Area B.", "Area C.", "Area D.", "Area E."):
        driver(name, (VEVEY, 5))
    request = _request(day=IN_TWO_DAYS)
    first = notify.notify_drivers(request)
    assert len(first) == 3
    # Twelve hours later, still no drive posted.
    created = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE open_requests SET created_at = ? WHERE id = ?", (created, request["id"]))
    assert notify.run_extra_rounds() == notify.EXTRA_ROUND_DRIVERS
    told = {n["recipient_name"] for n in db.notified_for_request(request["id"], "request_for_driver")}
    assert len(told) == 5 and set(first) < told


def test_the_passenger_hears_how_many_were_told(driver, user_named, force_haversine, fake_transit):
    driver("Zoe Z.", (VEVEY, 5))
    driver("Yann Y.", (FRIBOURG, 5))
    marie = user_named("Marie M.")
    result = request_routes.create_ride_request(MatchRequest(
        origin=VEVEY, destination=FRIBOURG, requested_time=f"{TOMORROW}T18:30",
    ), marie)
    assert result["drivers_told"] == 2
    assert result["demo_request"] is False


# --- The list of requests in a driver's areas --------------------------------------

def _listed(user):
    from routes import areas as area_routes
    return area_routes.get_requests_in_areas(user)


def test_a_driver_browses_the_requests_in_their_areas(driver, user_named):
    zoe = driver("Zoe Z.", (VEVEY, 5), (SION, 10))
    later = _request("Paul P.", origin=FRIBOURG, destination=VEVEY, day=IN_TWO_DAYS, at="08:00")
    sooner = _request("Marie M.", origin=VEVEY, destination=FRIBOURG, day=TOMORROW)
    _request("Lea L.", origin=FRIBOURG, destination=BULLE)  # touches none of her areas

    listed = _listed(zoe)
    # The soonest first, with what a notified driver would see and how it fits.
    assert [(r["id"], r["passenger_name"], r["ends"]) for r in listed] == [
        (sooner["id"], "Marie M.", ["origin"]), (later["id"], "Paul P.", ["destination"]),
    ]
    assert listed[0]["area"]["name"] == "Vevey"
    assert listed[0]["area_ids"] == [a["id"] for a in areas.list_for(zoe["id"]) if a["name"] == "Vevey"]
    assert (listed[0]["origin_name"], listed[0]["destination_name"]) == ("Vevey", "Fribourg")
    assert listed[0]["requested_time"] == f"{TOMORROW}T18:30"
    # Every request there, whether or not she was notified about it.
    assert notify.inbox("Zoe Z.")["notifications"] == []


def test_the_list_leaves_out_ones_own_cancelled_past_and_demo_requests(driver, monkeypatch):
    zoe = driver("Zoe Z.", (VEVEY, 5))
    _request("Zoe Z.")                                            # her own
    cancelled = _request("Paul P.")
    db.set_request_status(cancelled["id"], "cancelled")
    _request("Old O.", day=date.today() - timedelta(days=1))      # its time has passed
    demo = _request("Demo D.")
    shown = _request("Marie M.")
    monkeypatch.setattr(notify, "is_demo_request", lambda r: r["id"] == demo["id"])
    assert [r["id"] for r in _listed(zoe)] == [shown["id"]]


def test_no_areas_no_list(user_named):
    _request("Marie M.")
    assert _listed(user_named("Zoe Z.")) == []


def test_the_list_needs_a_session(isolated_db):
    from fastapi.testclient import TestClient

    import main
    resp = TestClient(main.app, base_url="https://testserver").get("/api/areas/requests")
    assert resp.status_code == 401
