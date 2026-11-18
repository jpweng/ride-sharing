"""
Bookings (bookings.py, routes/bookings.py): asking for seats on a drive,
the driver's answer, cancelling, seats and prices, and what happens when a
drive or an account goes. Isolated temp DB and haversine routing -- no
network. The route functions are called directly, as a given signed-in
user; test_auth.py covers the sessions and checks in front of them.
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import bookings
import database as db
import main
import maintenance
import notify
import pricing
from routes import auth as auth_routes
from routes import bookings as booking_routes
from routes import drives as drive_routes
from routes import match as match_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}
MORGES = {"name": "Morges", "lat": 46.5110, "lon": 6.4990}
NYON = {"name": "Nyon", "lat": 46.3833, "lon": 6.2396}
BERN = {"name": "Bern", "lat": 46.9480, "lon": 7.4474}
ZURICH = {"name": "Zürich", "lat": 47.3769, "lon": 8.5417}


@pytest.fixture
def users(user_named, force_haversine):
    """Jean drives; Marie, Paul and Lea ride."""
    return {name.split()[0].lower(): user_named(name) for name in ("Jean D.", "Marie M.", "Paul P.", "Lea L.")}


def _drive(driver, seats=3, origin=LAUSANNE, destination=GENEVE, at="08:00", **fields):
    return drive_routes.post_drive(drive_routes.DriveCreate(
        origin=origin, destination=destination, departure_time=f"{TOMORROW}T{at}", seats_available=seats, **fields,
    ), driver)


def _ask(passenger, drive, **fields):
    return booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(**fields), passenger)


def _accept(booking, driver, note=None):
    return booking_routes.accept_booking(booking["id"], driver, booking_routes.BookingAnswer(note=note))


def _code(exc):
    return exc.value.status_code, exc.value.detail["code"]


def _refused(call, *args, **kwargs):
    with pytest.raises(HTTPException) as exc:
        call(*args, **kwargs)
    return _code(exc)


def _inbox(user):
    """(kind, details) of the user's notifications, oldest first."""
    return [(n["kind"], n["details"]) for n in reversed(notify.inbox(user["name"])["notifications"])]


def _kinds(user):
    return [kind for kind, _ in _inbox(user)]


def _seats(drive):
    d = drive_routes.get_drive(drive["id"])
    return d["seats_available"], d["seats_booked"]


# --- Asking for a seat ---------------------------------------------------------

def test_asking_for_the_whole_drive(users):
    drive = _drive(users["jean"])
    booking = _ask(users["marie"], drive)

    assert booking["status"] == "requested"
    assert (booking["passenger_name"], booking["seats"]) == ("Marie M.", 1)
    assert (booking["origin_name"], booking["destination_name"]) == ("Lausanne", "Genève")
    assert booking["pickup"] == {"lat": 46.5197, "lon": 6.6323, "time": f"{TOMORROW}T08:00", "gap_km": 0.0}
    assert booking["dropoff"]["time"] > booking["pickup"]["time"]
    assert booking["ride_km"] == round(drive["route_distance_m"] / 1000, 1)
    assert booking["drive"]["driver_name"] == "Jean D."
    # A request holds no seat.
    assert _seats(drive) == (3, 0)
    # The driver is told.
    kind, details = _inbox(users["jean"])[-1]
    assert kind == "booking_requested"
    assert (details["booking_id"], details["passenger_name"], details["seats"]) == (booking["id"], "Marie M.", 1)
    assert details["drive"]["origin_name"] == "Lausanne"


def test_asking_for_part_of_the_drive_meets_on_its_route(users):
    drive = _drive(users["jean"])
    booking = _ask(users["marie"], drive, origin=MORGES, destination=NYON, note="  At the station, red jacket  ")

    assert (booking["origin_name"], booking["destination_name"]) == ("Morges", "Nyon")
    assert booking["passenger_note"] == "At the station, red jacket"
    # Picked up after the drive's departure, dropped off before its arrival,
    # at the points of the route nearest to Morges and Nyon (the route is a
    # straight line here, a few km from both).
    assert f"{TOMORROW}T08:00" < booking["pickup"]["time"] < booking["dropoff"]["time"]
    assert 0 < booking["pickup"]["gap_km"] < 10 and 0 < booking["dropoff"]["gap_km"] < 10
    assert 0 < booking["ride_km"] < drive["route_distance_m"] / 1000
    # And pays for the part they ride.
    whole = _ask(users["paul"], drive)
    assert booking["price_chf"] < whole["price_chf"]


def test_a_start_and_destination_the_drive_doesnt_pass(users):
    drive = _drive(users["jean"])
    assert _refused(_ask, users["marie"], drive, origin=BERN, destination=ZURICH) == (400, "not_on_route")
    # The wrong way round isn't a ride either.
    assert _refused(_ask, users["marie"], drive, origin=NYON, destination=MORGES) == (400, "not_on_route")


def test_both_ends_or_neither():
    with pytest.raises(ValidationError):
        booking_routes.BookingCreate(origin=MORGES)
    with pytest.raises(ValidationError):
        booking_routes.BookingCreate(seats=0)
    with pytest.raises(ValidationError):
        booking_routes.BookingCreate(note="x" * 301)


def test_what_cant_be_booked(users, monkeypatch):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean, seats=2)
    assert _refused(_ask, jean, drive) == (400, "own_drive")
    assert _refused(_ask, marie, drive, seats=3) == (400, "no_seats_left")
    assert _refused(booking_routes.request_booking, 999, booking_routes.BookingCreate(), marie) == (404, "drive_not_found")

    # A drive that has left.
    after = datetime.fromisoformat(f"{TOMORROW}T08:01")
    monkeypatch.setattr(notify, "local_now", lambda: after)
    assert _refused(_ask, marie, drive) == (400, "drive_departed")


def test_demo_drives_cant_be_booked(users):
    demo_id = db.create_drive(
        driver_name="Demo Driver", origin_name="Lausanne", origin_lat=46.5197, origin_lon=6.6323,
        destination_name="Genève", destination_lat=46.2044, destination_lon=6.1432,
        departure_time=f"{TOMORROW}T08:00", seats_available=3, route_geometry=None,
        route_distance_m=52000, route_duration_s=3000, route_source="haversine", is_demo=True,
    )
    assert _refused(booking_routes.request_booking, demo_id, booking_routes.BookingCreate(), users["marie"]) == (
        400, "not_bookable",
    )


def test_one_booking_per_passenger_and_drive(users):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean)
    # Not again while a request is waiting, or once it's accepted.
    first = _ask(marie, drive)
    assert _refused(_ask, marie, drive) == (400, "already_requested")
    _accept(first, jean)
    assert _refused(_ask, marie, drive) == (400, "already_requested")


def test_asking_again_after_a_decline_or_a_cancellation(users):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean, seats=2)

    def mine():
        return [(b["id"], b["status"]) for b in booking_routes.get_bookings(marie)["as_passenger"]]

    # After the driver declined: the passenger still sees it, and can ask again.
    declined = _ask(marie, drive)
    booking_routes.decline_booking(declined["id"], jean)
    assert mine() == [(declined["id"], "declined")]
    again = _ask(marie, drive, seats=2, note="Two of us this time")
    # The new request replaces the old booking, and the driver is told again.
    assert mine() == [(again["id"], "requested")]
    assert _kinds(jean)[-1] == "booking_requested"
    assert [b["id"] for b in booking_routes.get_bookings(jean)["as_driver"]] == [again["id"]]

    # After the driver cancelled a booking they had accepted.
    booking_routes.cancel_booking(_accept(again, jean)["id"], jean)
    assert mine() == [(again["id"], "cancelled_by_driver")]
    assert _seats(drive) == (2, 0)
    third = _ask(marie, drive)
    assert mine() == [(third["id"], "requested")]

    # After withdrawing it themselves.
    booking_routes.cancel_booking(third["id"], marie)
    assert mine() == []
    fourth = _accept(_ask(marie, drive), jean)
    assert mine() == [(fourth["id"], "accepted")]
    assert _seats(drive) == (1, 1)


# --- The driver's answer -------------------------------------------------------

def test_accepting_takes_the_seats_and_tells_the_passenger(users):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean, seats=3)
    booking = _ask(marie, drive, seats=2)

    accepted = _accept(booking, jean, note="White Golf, in front of the station")
    assert accepted["status"] == "accepted"
    assert accepted["driver_note"] == "White Golf, in front of the station"
    assert accepted["decided_at"]
    assert _seats(drive) == (1, 2)
    kind, details = _inbox(marie)[-1]
    assert kind == "booking_accepted"
    assert details["drive"]["driver_name"] == "Jean D."
    # Answered once.
    assert _refused(_accept, booking, jean) == (400, "booking_not_open")
    assert _refused(booking_routes.decline_booking, booking["id"], jean) == (400, "booking_not_open")


def test_declining_tells_the_passenger_and_takes_no_seat(users):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean)
    booking = _ask(marie, drive)
    assert booking_routes.decline_booking(booking["id"], jean)["status"] == "declined"
    assert _seats(drive) == (3, 0)
    assert _kinds(marie)[-1] == "booking_declined"
    # The passenger still sees it, the driver no longer does.
    assert [b["status"] for b in booking_routes.get_bookings(marie)["as_passenger"]] == ["declined"]
    assert booking_routes.get_bookings(jean)["as_driver"] == []


def test_a_full_drive_takes_nobody_else(users):
    jean = users["jean"]
    drive = _drive(jean, seats=1)
    first, second = _ask(users["marie"], drive), _ask(users["paul"], drive)
    _accept(first, jean)
    assert _seats(drive) == (0, 1)
    # The second request can't be accepted, and is left as it was.
    assert _refused(_accept, second, jean) == (400, "no_seats_left")
    assert [b["status"] for b in booking_routes.get_bookings(users["paul"])["as_passenger"]] == ["requested"]
    assert _refused(_ask, users["lea"], drive) == (400, "no_seats_left")
    # Until the seat is free again.
    booking_routes.cancel_booking(first["id"], users["marie"])
    assert _accept(second, jean)["status"] == "accepted"


def test_only_the_driver_answers(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    booking = _ask(marie, _drive(jean))
    for someone in (marie, paul):
        assert _refused(_accept, booking, someone) == (404, "booking_not_found")
        assert _refused(booking_routes.decline_booking, booking["id"], someone) == (404, "booking_not_found")
    assert _refused(booking_routes.accept_booking, 999, jean) == (404, "booking_not_found")


def test_no_answer_after_the_drive_left(users, monkeypatch):
    jean = users["jean"]
    booking = _ask(users["marie"], _drive(jean))
    monkeypatch.setattr(notify, "local_now", lambda: datetime.fromisoformat(f"{TOMORROW}T09:00"))
    assert _refused(_accept, booking, jean) == (400, "drive_departed")


# --- Cancelling ------------------------------------------------------------------

def test_the_passenger_withdraws_a_request(users):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean)
    booking = _ask(marie, drive)
    assert booking_routes.cancel_booking(booking["id"], marie)["status"] == "cancelled_by_passenger"
    assert _seats(drive) == (3, 0)
    kind, details = _inbox(jean)[-1]
    assert (kind, details["by"], details["was"]) == ("booking_cancelled", "passenger", "requested")
    # Gone from both pages.
    assert booking_routes.get_bookings(marie)["as_passenger"] == []
    assert booking_routes.get_bookings(jean)["as_driver"] == []
    assert _refused(booking_routes.cancel_booking, booking["id"], marie) == (400, "booking_not_open")


def test_cancelling_an_accepted_booking_frees_its_seats(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    drive = _drive(jean, seats=3)
    theirs = _accept(_ask(marie, drive, seats=2), jean)
    his = _accept(_ask(paul, drive), jean)
    assert _seats(drive) == (0, 3)

    # By the passenger: the driver is told.
    booking_routes.cancel_booking(theirs["id"], marie)
    assert _seats(drive) == (2, 1)
    kind, details = _inbox(jean)[-1]
    assert (kind, details["by"], details["was"], details["seats"]) == ("booking_cancelled", "passenger", "accepted", 2)

    # By the driver: the passenger is told, and still sees it.
    assert booking_routes.cancel_booking(his["id"], jean)["status"] == "cancelled_by_driver"
    assert _seats(drive) == (3, 0)
    kind, details = _inbox(paul)[-1]
    assert (kind, details["by"]) == ("booking_cancelled", "driver")
    assert [b["status"] for b in booking_routes.get_bookings(paul)["as_passenger"]] == ["cancelled_by_driver"]


def test_who_can_cancel_what(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    booking = _ask(marie, _drive(jean))
    assert _refused(booking_routes.cancel_booking, booking["id"], paul) == (404, "booking_not_found")
    # The driver declines a request; cancelling is for bookings they accepted.
    assert _refused(booking_routes.cancel_booking, booking["id"], jean) == (400, "booking_not_open")


# --- Seats and prices ------------------------------------------------------------

def test_the_price_falls_as_passengers_are_accepted(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    drive = _drive(jean, seats=3)
    km = drive["route_distance_m"] / 1000
    top = pricing.band(km)["max_chf"]
    drive = _drive(jean, seats=3, at="09:00", price_chf=top)
    by_passengers = drive["price_by_passengers"]
    assert by_passengers[0] > by_passengers[1] > by_passengers[2]

    first = _ask(marie, drive)
    assert first["price_chf"] == by_passengers[0]  # alone, if accepted
    first = _accept(first, jean)
    second = _ask(paul, drive, seats=2)
    assert second["price_chf"] == by_passengers[2]  # three in the car, if accepted
    assert second["total_chf"] == round(2 * by_passengers[2], 2)
    _accept(second, jean)

    # Everyone now pays the price for three, the first passenger too.
    marie_sees = booking_routes.get_bookings(marie)["as_passenger"][0]
    assert marie_sees["price_chf"] == by_passengers[2]
    assert [b["price_chf"] for b in booking_routes.get_bookings(jean)["as_driver"]] == [by_passengers[2]] * 2


def test_drives_show_free_and_booked_seats_and_the_next_passengers_price(users):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean, seats=3, price_chf=pricing.band(_drive(jean, at="07:00")["route_distance_m"] / 1000)["max_chf"])
    by_passengers = drive["price_by_passengers"]
    _accept(_ask(marie, drive), jean)

    shown = drive_routes.get_drive(drive["id"])
    assert (shown["seats_available"], shown["seats_booked"]) == (2, 1)
    assert shown["price_by_passengers"] == by_passengers  # still for 1, 2, 3 passengers
    assert shown["price_chf"] == by_passengers[1]  # the next passenger is the second
    listed = {d["id"]: d for d in drive_routes.get_drives(None)}[drive["id"]]
    assert (listed["seats_available"], listed["seats_booked"], listed["price_chf"]) == (2, 1, by_passengers[1])


def test_a_full_drive_is_no_longer_found_by_a_search(users, fake_transit):
    jean = users["jean"]
    drive = _drive(jean, seats=1)
    search = match_routes.MatchRequest(origin=LAUSANNE, destination=GENEVE, requested_time=f"{TOMORROW}T08:00")
    found = match_routes.match_ride(search)["driver_matches"]
    assert [(m["id"], m["seats_available"], m["seats_booked"]) for m in found] == [(drive["id"], 1, 0)]

    booking = _accept(_ask(users["marie"], drive), jean)
    assert match_routes.match_ride(search)["driver_matches"] == []
    booking_routes.cancel_booking(booking["id"], users["marie"])
    assert len(match_routes.match_ride(search)["driver_matches"]) == 1


# --- The pages -------------------------------------------------------------------

def test_each_sees_their_own_bookings(users):
    jean, marie, paul, lea = (users[n] for n in ("jean", "marie", "paul", "lea"))
    early, late = _drive(jean, at="07:00"), _drive(jean, at="18:00")
    on_late = _ask(marie, late)
    on_early = _accept(_ask(marie, early), jean)
    pauls = _ask(paul, late)

    assert [b["id"] for b in booking_routes.get_bookings(marie)["as_passenger"]] == [on_early["id"], on_late["id"]]
    assert booking_routes.get_bookings(marie)["as_driver"] == []
    assert [b["id"] for b in booking_routes.get_bookings(jean)["as_driver"]] == [
        on_early["id"], on_late["id"], pauls["id"],
    ]
    assert booking_routes.get_bookings(lea) == {"as_passenger": [], "as_driver": []}


def test_bookings_leave_the_pages_a_day_after_the_drive(users, monkeypatch):
    jean, marie = users["jean"], users["marie"]
    _accept(_ask(marie, _drive(jean)), jean)
    departure = datetime.fromisoformat(f"{TOMORROW}T08:00")
    assert len(bookings.for_user(marie, departure + timedelta(hours=23))["as_passenger"]) == 1
    assert bookings.for_user(marie, departure + timedelta(hours=25))["as_passenger"] == []


# --- When a drive or an account goes -----------------------------------------------

def test_cancelling_the_drive_tells_its_passengers_once(users, fake_transit):
    jean, marie, paul, lea = (users[n] for n in ("jean", "marie", "paul", "lea"))
    # Lea had asked for this ride, so she was told when the drive was posted.
    request = db.create_open_request("Lea L.", LAUSANNE, GENEVE, f"{TOMORROW}T08:00", 5, 5, 120)
    drive = _drive(jean)
    assert _kinds(lea) == ["drive_for_passenger"]
    _accept(_ask(marie, drive), jean)
    _ask(paul, drive)
    booking_routes.decline_booking(_ask(lea, drive)["id"], jean)

    drive_routes.cancel_drive(drive["id"], jean)

    kind, details = _inbox(marie)[-1]
    assert (kind, details["was"], details["drive"]["destination_name"]) == ("booking_drive_cancelled", "accepted", "Genève")
    assert _kinds(paul)[-1] == "booking_drive_cancelled"
    # Lea's request was declined: she hears as someone who was told about the drive.
    assert _kinds(lea)[-1] == "drive_cancelled"
    assert request["id"]
    # The bookings went with the drive.
    for user in (marie, paul, lea):
        assert booking_routes.get_bookings(user)["as_passenger"] == []
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0


def test_a_passenger_with_a_seat_who_was_also_told_hears_once(users, fake_transit):
    jean, marie = users["jean"], users["marie"]
    db.create_open_request("Marie M.", LAUSANNE, GENEVE, f"{TOMORROW}T08:00", 5, 5, 120)
    drive = _drive(jean)
    _accept(_ask(marie, drive), jean)
    drive_routes.cancel_drive(drive["id"], jean)
    assert _kinds(marie) == ["drive_for_passenger", "booking_accepted", "booking_drive_cancelled"]


def _delete_account(user):
    class _Response:
        def delete_cookie(self, *args, **kwargs):
            pass

    return auth_routes.delete_account(_Response(), {"user": user, "session": {"is_demo": 0}})


def test_a_passenger_deleting_their_account_frees_their_seats(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    drive = _drive(jean, seats=3)
    _accept(_ask(marie, drive, seats=2), jean)
    _accept(_ask(paul, drive), jean)
    assert _seats(drive) == (0, 3)

    _delete_account(marie)

    assert _seats(drive) == (2, 1)
    kind, details = _inbox(jean)[-1]
    assert (kind, details["by"], details["passenger_name"]) == ("booking_cancelled", "passenger", "Marie M.")
    assert [b["passenger_name"] for b in booking_routes.get_bookings(jean)["as_driver"]] == ["Paul P."]


def test_a_driver_deleting_their_account_tells_their_passengers(users):
    jean, marie = users["jean"], users["marie"]
    _accept(_ask(marie, _drive(jean)), jean)
    _delete_account(jean)
    assert _kinds(marie)[-1] == "booking_drive_cancelled"
    assert booking_routes.get_bookings(marie)["as_passenger"] == []


def test_old_drives_are_purged_with_their_bookings(users):
    jean, marie = users["jean"], users["marie"]
    drive = _drive(jean)
    _accept(_ask(marie, drive), jean)
    later = datetime.fromisoformat(f"{TOMORROW}T08:00") + maintenance.RETENTION + timedelta(days=1)
    assert maintenance.purge_old_data(later)["drives"] == 1
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0


def test_a_name_with_a_booking_is_kept(users):
    # Names without an account are deleted once nothing references them; a
    # booking does.
    jean, marie = users["jean"], users["marie"]
    _ask(marie, _drive(jean))
    db.delete_inactive_users()
    assert db.get_user(marie["id"]) is not None


# --- Sessions --------------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("post", "/api/drives/1/bookings"),
    ("get", "/api/bookings"),
    ("post", "/api/bookings/1/accept"),
    ("post", "/api/bookings/1/decline"),
    ("post", "/api/bookings/1/cancel"),
])
def test_bookings_need_a_session(isolated_db, method, path):
    resp = TestClient(main.app, base_url="https://testserver").request(method, path, json={} if method == "post" else None)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "not_signed_in"
