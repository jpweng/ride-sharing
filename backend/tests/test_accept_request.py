"""
A driver accepting a passenger's ride request directly
(bookings.accept_request, POST /api/requests/{id}/accept): the drive is
posted and the passenger booked on it in one step, and the first driver to
accept gets it. Isolated temp DB and haversine routing -- no network.
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import database as db
import notify
import pricing
from routes import bookings as booking_routes
from routes import drives as drive_routes
from routes import ride_requests as request_routes
from routes.match import MatchRequest

IN_TWO_DAYS = (date.today() + timedelta(days=2)).isoformat()

ZUG = {"name": "Zug", "lat": 47.1724, "lon": 8.5174}
RAPPERSWIL = {"name": "Rapperswil", "lat": 47.2266, "lon": 8.8184}
LUZERN = {"name": "Luzern", "lat": 47.0502, "lon": 8.3093}
ZURICH = {"name": "Zürich", "lat": 47.3769, "lon": 8.5417}
BERN = {"name": "Bern", "lat": 46.9480, "lon": 7.4474}
THUN = {"name": "Thun", "lat": 46.7580, "lon": 7.6280}


@pytest.fixture
def users(user_named, force_haversine, fake_transit):
    return {name.split()[0].lower(): user_named(name) for name in ("Zoe Z.", "Yann Y.", "Marie M.", "Paul P.")}


def _ask(passenger, origin=ZUG, destination=RAPPERSWIL, at="07:30"):
    return request_routes.create_ride_request(MatchRequest(
        origin=origin, destination=destination, requested_time=f"{IN_TWO_DAYS}T{at}",
    ), passenger)["request"]


def _accept(driver, request, **fields):
    return request_routes.accept_ride_request(request["id"], request_routes.RequestAccept(**fields), driver)


def _refused(call, *args, **kwargs):
    with pytest.raises(HTTPException) as exc:
        call(*args, **kwargs)
    return exc.value.status_code, exc.value.detail["code"]


def _inbox(user):
    return [(n["kind"], n) for n in reversed(notify.inbox(user["name"])["notifications"])]


def test_accepting_posts_the_drive_and_books_the_passenger(users):
    zoe, marie = users["zoe"], users["marie"]
    request = _ask(marie)

    booking = _accept(zoe, request, seats_available=4, note="Grey Skoda, at the station's north exit")

    # The passenger's own trip at their time, as a drive of the driver's...
    [drive] = drive_routes.get_drives({"user": zoe, "session": {"is_demo": 0}, "extended": False}, mine=True)
    assert (drive["origin_name"], drive["destination_name"], drive["departure_time"]) == (
        "Zug", "Rapperswil", f"{IN_TWO_DAYS}T07:30",
    )
    assert (drive["seats_available"], drive["seats_booked"]) == (3, 1)
    assert drive["driver_price_chf"] == pricing.band(drive["route_distance_m"] / 1000)["default_chf"]
    # ...with the passenger booked on it, accepted, for the whole of it.
    assert (booking["status"], booking["passenger_name"], booking["seats"]) == ("accepted", "Marie M.", 1)
    assert booking["drive_id"] == drive["id"] and booking["decided_at"]
    assert booking["driver_note"] == "Grey Skoda, at the station's north exit"
    assert booking["pickup"]["time"] == f"{IN_TWO_DAYS}T07:30" and booking["pickup"]["gap_km"] == 0
    assert booking["price_chf"] == drive["price_by_passengers"][0]
    assert [b["id"] for b in booking_routes.get_bookings(marie)["as_passenger"]] == [booking["id"]]
    assert [b["id"] for b in booking_routes.get_bookings(zoe)["as_driver"]] == [booking["id"]]

    # The request is closed, and the passenger is told who drives them.
    assert request_routes.get_ride_requests_open(marie) == []
    kind, n = _inbox(marie)[-1]
    assert kind == "request_accepted"
    assert n["details"]["drive"]["driver_name"] == "Zoe Z." and n["request"]["status"] == "accepted"
    # The driver isn't told about their own acceptance.
    assert [k for k, _ in _inbox(zoe)] == []


def test_the_first_driver_to_accept_gets_it(users):
    request = _ask(users["marie"])
    _accept(users["zoe"], request)
    assert _refused(_accept, users["yann"], request) == (400, "request_not_open")
    # Nothing was posted for the second driver.
    assert drive_routes.get_drives({"user": users["yann"], "session": {"is_demo": 0}, "extended": False}, mine=True) == []
    assert len(booking_routes.get_bookings(users["marie"])["as_passenger"]) == 1


def test_what_cant_be_accepted(users, monkeypatch):
    zoe, marie = users["zoe"], users["marie"]
    request = _ask(marie)
    assert _refused(_accept, marie, request) == (400, "own_request")
    assert _refused(request_routes.accept_ride_request, 999, request_routes.RequestAccept(), zoe) == (404, "request_not_found")

    cancelled = _ask(users["paul"])
    request_routes.cancel_ride_request(cancelled["id"], users["paul"])
    assert _refused(_accept, zoe, cancelled) == (400, "request_not_open")

    # A demo request is a fictional need.
    real = notify.is_demo_request
    monkeypatch.setattr(notify, "is_demo_request", lambda r: r["id"] == request["id"])
    assert _refused(_accept, zoe, request) == (400, "request_not_open")
    monkeypatch.setattr(notify, "is_demo_request", real)

    # Its time has passed.
    monkeypatch.setattr(notify, "local_now", lambda: datetime.fromisoformat(f"{IN_TWO_DAYS}T08:00"))
    assert _refused(_accept, zoe, request) == (400, "request_not_open")


def test_with_the_drivers_own_trip_the_passenger_is_picked_up_on_the_way(users):
    zoe, marie = users["zoe"], users["marie"]
    request = _ask(marie)   # Zug -> Rapperswil at 07:30
    # Zoe usually drives Luzern -> Rapperswil, through Zug, leaving at 07:10.
    booking = _accept(zoe, request, origin=LUZERN, destination=RAPPERSWIL, departure_time=f"{IN_TWO_DAYS}T07:10")

    assert (booking["drive"]["origin_name"], booking["drive"]["destination_name"]) == ("Luzern", "Rapperswil")
    assert booking["drive"]["departure_time"] == f"{IN_TWO_DAYS}T07:10"
    # The passenger rides their own part of it.
    assert (booking["origin_name"], booking["destination_name"]) == ("Zug", "Rapperswil")
    assert f"{IN_TWO_DAYS}T07:10" < booking["pickup"]["time"] < booking["dropoff"]["time"]
    assert 0 < booking["ride_km"] < booking["drive"]["route_distance_m"] / 1000
    assert booking["status"] == "accepted"


def test_the_drivers_own_trip_must_serve_the_request(users):
    zoe, marie = users["zoe"], users["marie"]
    request = _ask(marie)
    elsewhere = dict(origin=BERN, destination=THUN, departure_time=f"{IN_TWO_DAYS}T07:10")
    assert _refused(_accept, zoe, request, **elsewhere) == (400, "not_on_route")
    too_late = dict(origin=LUZERN, destination=RAPPERSWIL, departure_time=f"{IN_TWO_DAYS}T15:00")
    assert _refused(_accept, zoe, request, **too_late) == (400, "not_on_route")
    # The request is still open for someone else.
    assert [r["id"] for r in request_routes.get_ride_requests_open(marie)] == [request["id"]]
    assert _accept(users["yann"], request)["status"] == "accepted"


def test_the_price_is_the_drivers_within_the_band(users):
    zoe, marie = users["zoe"], users["marie"]
    request = _ask(marie)
    assert _refused(_accept, zoe, request, price_chf=500) == (400, "price_too_high")
    assert [r["id"] for r in request_routes.get_ride_requests_open(marie)] == [request["id"]]
    km = 23.6  # roughly; the band comes from the drive's own distance
    booking = _accept(zoe, request, price_chf=pricing.band(km)["min_chf"] + 1)
    assert booking["status"] == "accepted"


def test_a_whole_trip_or_none():
    with pytest.raises(ValidationError):
        request_routes.RequestAccept(origin=LUZERN)
    with pytest.raises(ValidationError):
        request_routes.RequestAccept(origin=LUZERN, destination=RAPPERSWIL)
    with pytest.raises(ValidationError):
        request_routes.RequestAccept(seats_available=0)


def test_other_waiting_passengers_hear_of_the_drive(users):
    marie, paul = users["marie"], users["paul"]
    first, second = _ask(marie), _ask(paul, at="07:40")
    _accept(users["zoe"], first, seats_available=3)
    # Paul's request stays open, and he is told a drive that fits was posted.
    assert [r["id"] for r in request_routes.get_ride_requests_open(paul)] == [second["id"]]
    assert [k for k, _ in _inbox(paul)] == ["drive_for_passenger"]


def test_the_passenger_can_cancel_and_the_driver_keeps_the_drive(users):
    zoe, marie = users["zoe"], users["marie"]
    booking = _accept(zoe, _ask(marie))
    booking_routes.cancel_booking(booking["id"], marie)
    drive = drive_routes.get_drive(booking["drive_id"])
    assert (drive["seats_available"], drive["seats_booked"]) == (3, 0)
    assert [k for k, _ in _inbox(zoe)] == ["booking_cancelled"]


def test_other_drivers_notifications_say_its_no_longer_open(users, monkeypatch):
    # Yann was told about the request; Zoe takes it.
    import areas
    assert areas.add(users["yann"]["id"], "Zug", ZUG["lat"], ZUG["lon"], 5)
    request = _ask(users["marie"])
    assert [k for k, _ in _inbox(users["yann"])] == ["request_for_driver"]
    _accept(users["zoe"], request)
    _, n = _inbox(users["yann"])[-1]
    assert n["request"]["status"] == "accepted"
    assert notify.requests_in_areas(users["yann"]) == []
