"""
Bookings: a passenger asks for seats on a posted drive, the driver accepts
or declines, and either of them can cancel afterwards. See docs/booking.md.

  requested --driver accepts--> accepted --either cancels--> cancelled_by_...
            --driver declines-> declined
            --passenger withdraws--> cancelled_by_passenger

Seats: drives.seats_available is the number of free seats. A request holds
no seat; accepting takes the booking's seats from the drive (in one
transaction, which refuses to go below zero, so a drive is never
overbooked), and cancelling an accepted booking gives them back. A drive
with no free seat no longer shows up in searches.

Where they meet: a booking made from a search keeps the passenger's own
start and destination, and the points where the drive's route passes
nearest to them, with the times there (matching.match_drive, the same as
the search showed). A booking made from the list of drives is for the whole
drive. The passenger can add a note for the driver with the request, and
the driver one for the passenger when accepting, to agree on the exact
spot; no contact details are exchanged.

Price: nothing is charged; the app shows what each passenger pays per seat
by pricing.py's rules, which falls when more passengers are accepted.

Demo drives can't be booked: they're fictional. A passenger has one booking
per drive: while it's requested or accepted they can't ask again; once it's
declined or cancelled, by either of them, they can, and the new request
replaces the old booking.

Both sides hear about each step in their inbox (notify.send): the kinds
booking_requested (to the driver), booking_accepted and booking_declined
(to the passenger), booking_cancelled (to the other one), and
booking_drive_cancelled (to the passengers of a cancelled drive, whose
bookings are deleted with it).

The other way round, a driver can accept a passenger's ride request
(notify.py) directly: accept_request posts the drive and books the
passenger on it in one step, and the request is closed. The first driver
to accept gets it; the passenger hears of it (request_accepted) and can
cancel the booking like any other.

Errors are raised as the API's own (api.api_error), for the routes in
routes/bookings.py.
"""
from datetime import datetime, timedelta

import database as db
import matching
import notify
import pricing
import search
from api import api_error

MAX_NOTE_CHARS = 300
MAX_SEATS = 8

# A booking's start and destination can be as far from the drive's route as
# a search looks at its widest: a ride found by a search can be booked.
MAX_GAP_KM = search.WIDER_RADIUS_KM

ACTIVE = ("requested", "accepted")
# Statuses a passenger sees on their page (their own cancellations are gone),
# and a driver on theirs.
SHOWN_TO_PASSENGER = ("requested", "accepted", "declined", "cancelled_by_driver")
SHOWN_TO_DRIVER = ACTIVE

# Bookings stay on both pages until this long after the drive's departure.
SHOWN_AFTER_DEPARTURE = timedelta(hours=24)

# What both sides see of a booking's drive (no route: the map asks for it).
_DRIVE_COLUMNS = (
    "id", "driver_id", "driver_name", "origin_name", "origin_lat", "origin_lon",
    "destination_name", "destination_lat", "destination_lon", "departure_time",
    "seats_available", "route_distance_m", "route_duration_s", "is_demo", "driver_price_chf",
)
_DRIVE_SHOWN = tuple(c for c in _DRIVE_COLUMNS if c != "driver_price_chf")


def _naive(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _in(ids) -> str:
    return ",".join("?" * len(ids))


# ---------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------
def seats_booked(drive_ids: list[int]) -> dict[int, int]:
    """drive id -> seats of its accepted bookings (drives without any are left out)."""
    booked = {}
    with db.get_conn() as conn:
        for i in range(0, len(drive_ids), 500):  # stay under SQLite's variable limit
            chunk = drive_ids[i:i + 500]
            rows = conn.execute(
                f"""
                SELECT drive_id, SUM(seats) AS seats FROM bookings
                WHERE status = 'accepted' AND drive_id IN ({_in(chunk)}) GROUP BY drive_id
                """,
                chunk,
            )
            booked.update({r["drive_id"]: r["seats"] for r in rows})
    return booked


def with_seats_booked(drives: list[dict]) -> list[dict]:
    """The drives (or matches), each with seats_booked: the seats already
    taken, next to seats_available, the free ones. pricing.py prices the
    next passenger with it."""
    booked = seats_booked([d["id"] for d in drives if not d.get("is_demo")])
    for d in drives:
        d["seats_booked"] = booked.get(d["id"], 0)
    return drives


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------
def _drives(conn, drive_ids: list[int]) -> dict[int, dict]:
    if not drive_ids:
        return {}
    rows = conn.execute(
        f"SELECT {', '.join(_DRIVE_COLUMNS)} FROM drives WHERE id IN ({_in(drive_ids)})", drive_ids
    )
    return {r["id"]: dict(r) for r in rows}


def _get(booking_id: int) -> tuple[dict, dict]:
    """(booking, its drive), or 404."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        drive = _drives(conn, [row["drive_id"]]).get(row["drive_id"]) if row else None
    if row is None or drive is None:
        raise _not_found()
    return dict(row), drive


def _not_found():
    # Also for a booking the user has no part in: whether an id exists is
    # nobody else's business.
    return api_error(404, "booking_not_found", "Booking not found")


def view(booking: dict, drive: dict, accepted_seats: int) -> dict:
    """A booking as the API gives it to its passenger and its driver, with
    its drive and the price per seat: with the passengers accepted so far,
    this one included (so a request shows what it would cost if accepted)."""
    passengers = accepted_seats if booking["status"] == "accepted" else accepted_seats + booking["seats"]
    price = pricing.booking_price_chf(drive, booking["ride_km"], passengers)
    return {
        **{k: booking[k] for k in (
            "id", "drive_id", "status", "seats", "passenger_id", "passenger_name", "origin_name", "destination_name",
            "ride_km", "passenger_note", "driver_note", "created_at", "decided_at", "cancelled_at",
        )},
        "pickup": {
            "lat": booking["pickup_lat"], "lon": booking["pickup_lon"],
            "time": booking["pickup_time"], "gap_km": booking["pickup_gap_km"],
        },
        "dropoff": {
            "lat": booking["dropoff_lat"], "lon": booking["dropoff_lon"],
            "time": booking["dropoff_time"], "gap_km": booking["dropoff_gap_km"],
        },
        "price_chf": price,
        "total_chf": round(price * booking["seats"], 2) if price is not None else None,
        "drive": {k: drive[k] for k in _DRIVE_SHOWN},
    }


def _view(booking_id: int) -> dict:
    booking, drive = _get(booking_id)
    return view(booking, drive, seats_booked([drive["id"]]).get(drive["id"], 0))


def for_user(user: dict, now: datetime | None = None) -> dict:
    """The user's bookings on drives that haven't left more than
    SHOWN_AFTER_DEPARTURE ago, the soonest first: {"as_passenger": theirs,
    "as_driver": the requests and accepted bookings on their drives}."""
    now = now or notify.local_now()
    since = _iso(now - SHOWN_AFTER_DEPARTURE)
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT b.*, d.driver_id FROM bookings b JOIN drives d ON d.id = b.drive_id
            WHERE (b.passenger_id = ? OR d.driver_id = ?) AND d.departure_time > ?
            ORDER BY d.departure_time, b.id
            """,
            (user["id"], user["id"], since),
        )]
        drives = _drives(conn, sorted({r["drive_id"] for r in rows}))
    booked = seats_booked(list(drives))

    def shown(row):
        return view(row, drives[row["drive_id"]], booked.get(row["drive_id"], 0))

    return {
        "as_passenger": [
            shown(r) for r in rows if r["passenger_id"] == user["id"] and r["status"] in SHOWN_TO_PASSENGER
        ],
        "as_driver": [
            shown(r) for r in rows if r["driver_id"] == user["id"] and r["status"] in SHOWN_TO_DRIVER
        ],
    }


# ---------------------------------------------------------------------
# Asking for seats
# ---------------------------------------------------------------------
def _ride(drive: dict, origin: dict | None, destination: dict | None) -> dict:
    """Where the passenger gets in and out, when, and how far they ride: the
    whole drive without a start and destination of their own, else the
    points of the drive's route nearest to them."""
    departure = _naive(drive["departure_time"])
    if origin is None or destination is None:
        km = (drive.get("route_distance_m") or 0) / 1000.0 or matching.haversine_km(
            drive["origin_lat"], drive["origin_lon"], drive["destination_lat"], drive["destination_lon"]
        )
        duration_s = drive.get("route_duration_s") or km / matching.FALLBACK_SPEED_KMH * 3600.0
        ends = {
            "origin": {k: drive[f"origin_{k}"] for k in ("name", "lat", "lon")},
            "destination": {k: drive[f"destination_{k}"] for k in ("name", "lat", "lon")},
        }
        return {
            **ends,
            "pickup": {**ends["origin"], "time": _iso(departure), "gap_km": 0.0},
            "dropoff": {**ends["destination"], "time": _iso(departure + timedelta(seconds=duration_s)), "gap_km": 0.0},
            "ride_km": round(km, 1),
        }
    m = matching.match_drive(
        # Whether there are seats left is checked with the request itself.
        {**drive, "seats_available": 1},
        (origin["lat"], origin["lon"]), (destination["lat"], destination["lon"]),
        drive["departure_time"], MAX_GAP_KM, MAX_GAP_KM, matching.MAX_DRIVE_HOURS * 60,
    )
    if m is None:
        raise api_error(400, "not_on_route", "This drive doesn't pass near that start and destination")
    return {
        "origin": origin, "destination": destination,
        "pickup": m["pickup"], "dropoff": m["dropoff"], "ride_km": m["ride_km"],
    }


def _note(text: str | None) -> str | None:
    text = (text or "").strip()
    return text[:MAX_NOTE_CHARS] or None


def _details(booking: dict, drive: dict, **more) -> dict:
    """What a booking's notifications keep: enough to show after the booking
    and its drive are gone."""
    return {
        "booking_id": booking["id"],
        "passenger_name": booking["passenger_name"],
        "seats": booking["seats"],
        "pickup_time": booking["pickup_time"],
        "drive": {k: drive[k] for k in ("id", "driver_name", "origin_name", "destination_name", "departure_time")},
        **more,
    }


def _insert(conn, drive_id: int, passenger_id: int, passenger_name: str, ride: dict, seats: int,
            passenger_note: str | None = None, driver_note: str | None = None, accepted: bool = False) -> int:
    """A new booking row from _ride()'s answer; its id. `accepted`: made by
    the driver's own acceptance of a ride request (accept_request)."""
    now = db.now_iso()
    return conn.execute(
        """
        INSERT INTO bookings (
            drive_id, passenger_id, passenger_name, seats, status,
            origin_name, origin_lat, origin_lon, destination_name, destination_lat, destination_lon,
            pickup_lat, pickup_lon, pickup_time, pickup_gap_km,
            dropoff_lat, dropoff_lon, dropoff_time, dropoff_gap_km,
            ride_km, passenger_note, driver_note, created_at, decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            drive_id, passenger_id, passenger_name, seats, "accepted" if accepted else "requested",
            ride["origin"]["name"], ride["origin"]["lat"], ride["origin"]["lon"],
            ride["destination"]["name"], ride["destination"]["lat"], ride["destination"]["lon"],
            ride["pickup"]["lat"], ride["pickup"]["lon"], ride["pickup"]["time"], ride["pickup"]["gap_km"],
            ride["dropoff"]["lat"], ride["dropoff"]["lon"], ride["dropoff"]["time"], ride["dropoff"]["gap_km"],
            ride["ride_km"], _note(passenger_note), _note(driver_note), now, now if accepted else None,
        ),
    ).lastrowid


def request(drive: dict, passenger: dict, origin: dict | None = None, destination: dict | None = None,
            seats: int = 1, note: str | None = None, now: datetime | None = None) -> dict:
    """`passenger` asks for `seats` on `drive` (with its route geometry),
    from `origin` to `destination` ({name, lat, lon}), or for the whole
    drive without them. The driver is told. Returns the booking."""
    now = now or notify.local_now()
    if drive["is_demo"]:
        raise api_error(400, "not_bookable", "Demo drives are fictional and can't be booked")
    if drive["driver_id"] == passenger["id"]:
        raise api_error(400, "own_drive", "This is your own drive")
    if _naive(drive["departure_time"]) <= now:
        raise api_error(400, "drive_departed", "This drive has already left")
    ride = _ride(drive, origin, destination)

    with db.get_conn() as conn:
        free = conn.execute("SELECT seats_available FROM drives WHERE id = ?", (drive["id"],)).fetchone()
        if free is None:
            raise api_error(404, "drive_not_found", "Drive not found")
        if free["seats_available"] < seats:
            raise api_error(400, "no_seats_left", "This drive doesn't have that many free seats")
        if conn.execute(
            f"SELECT 1 FROM bookings WHERE drive_id = ? AND passenger_id = ? AND status IN ({_in(ACTIVE)})",
            (drive["id"], passenger["id"], *ACTIVE),
        ).fetchone():
            raise api_error(400, "already_requested", "You have already asked for a seat on this drive")
        # Asking again after a decline or a cancellation: the new request
        # replaces the old booking, so there is one per passenger and drive.
        conn.execute("DELETE FROM bookings WHERE drive_id = ? AND passenger_id = ?", (drive["id"], passenger["id"]))
        booking_id = _insert(conn, drive["id"], passenger["id"], passenger["name"], ride, seats, passenger_note=note)
    booking, drive = _get(booking_id)
    notify.send(drive["driver_name"], "booking_requested", _details(booking, drive))
    return _view(booking_id)


# ---------------------------------------------------------------------
# The driver's answer, and cancelling
# ---------------------------------------------------------------------
def _for_driver(booking_id: int, driver: dict) -> tuple[dict, dict]:
    booking, drive = _get(booking_id)
    if drive["driver_id"] != driver["id"]:
        raise _not_found()
    return booking, drive


def _not_open():
    return api_error(400, "booking_not_open", "This booking was already answered or cancelled")


def accept(booking_id: int, driver: dict, note: str | None = None, now: datetime | None = None) -> dict:
    """The driver accepts a request: its seats are taken from the drive, and
    the passenger is told."""
    now = now or notify.local_now()
    booking, drive = _for_driver(booking_id, driver)
    if _naive(drive["departure_time"]) <= now:
        raise api_error(400, "drive_departed", "This drive has already left")
    with db.get_conn() as conn:
        # Both or neither: an error leaves the transaction uncommitted.
        answered = conn.execute(
            """
            UPDATE bookings SET status = 'accepted', decided_at = ?, driver_note = ?
            WHERE id = ? AND status = 'requested'
            """,
            (db.now_iso(), _note(note), booking_id),
        )
        if answered.rowcount != 1:
            raise _not_open()
        taken = conn.execute(
            "UPDATE drives SET seats_available = seats_available - ? WHERE id = ? AND seats_available >= ?",
            (booking["seats"], drive["id"], booking["seats"]),
        )
        if taken.rowcount != 1:
            raise api_error(400, "no_seats_left", "This drive doesn't have that many free seats")
    notify.send(booking["passenger_name"], "booking_accepted", _details(booking, drive))
    return _view(booking_id)


def decline(booking_id: int, driver: dict) -> dict:
    """The driver declines a request; the passenger is told."""
    booking, drive = _for_driver(booking_id, driver)
    with db.get_conn() as conn:
        answered = conn.execute(
            "UPDATE bookings SET status = 'declined', decided_at = ? WHERE id = ? AND status = 'requested'",
            (db.now_iso(), booking_id),
        )
        if answered.rowcount != 1:
            raise _not_open()
    notify.send(booking["passenger_name"], "booking_declined", _details(booking, drive))
    return _view(booking_id)


def cancel(booking_id: int, user: dict) -> dict:
    """The passenger withdraws their request or cancels their accepted
    booking, or the driver cancels a booking they accepted (a request, they
    decline). An accepted booking's seats are free again, and the other one
    is told."""
    booking, drive = _get(booking_id)
    if booking["passenger_id"] == user["id"]:
        by, allowed = "passenger", ACTIVE
    elif drive["driver_id"] == user["id"]:
        by, allowed = "driver", ("accepted",)
    else:
        raise _not_found()
    if booking["status"] not in allowed:
        raise _not_open()
    with db.get_conn() as conn:
        cancelled = conn.execute(
            "UPDATE bookings SET status = ?, cancelled_at = ? WHERE id = ? AND status = ?",
            (f"cancelled_by_{by}", db.now_iso(), booking_id, booking["status"]),
        )
        if cancelled.rowcount != 1:
            raise _not_open()
        if booking["status"] == "accepted":
            conn.execute(
                "UPDATE drives SET seats_available = seats_available + ? WHERE id = ?",
                (booking["seats"], drive["id"]),
            )
    other = drive["driver_name"] if by == "passenger" else booking["passenger_name"]
    notify.send(other, "booking_cancelled", _details(booking, drive, by=by, was=booking["status"]))
    return _view(booking_id)


# ---------------------------------------------------------------------
# Accepting a passenger's ride request directly
# ---------------------------------------------------------------------
DEFAULT_SEATS_OFFERED = 3


def _request_not_open():
    return api_error(400, "request_not_open", "This ride request is no longer open")


def accept_request(request: dict, driver: dict, seats_available: int = DEFAULT_SEATS_OFFERED,
                   note: str | None = None, trip: dict | None = None, price_chf: float | None = None,
                   now: datetime | None = None) -> dict:
    """
    `driver` takes a passenger's open ride request: a drive is posted for
    them and the passenger is booked on it, accepted, in one step. The
    request is closed, so the first driver to accept gets it; the passenger
    is told (request_accepted), and so are the passengers of other open
    requests the drive fits, as for any posted drive.

    The drive is the passenger's own trip at their time (for a time to
    arrive by: leaving at the request's leave_by), or `trip`, the driver's
    own ({"origin", "destination": {name, lat, lon},
    "departure_time"}: their usual trip, from the notification), which must
    serve the request as a search would find it. `seats_available`: the
    seats the driver offers in all, the passenger's included. The price is
    the driver's `price_chf`, inside the band, or the default suggestion.
    Returns the booking.
    """
    now = now or notify.local_now()
    if request["status"] != "open" or _naive(request["requested_time"]) <= now or notify.is_demo_request(request):
        raise _request_not_open()
    if request["passenger_id"] == driver["id"]:
        raise api_error(400, "own_request", "This is your own ride request")

    theirs = {
        "origin": {k: request[f"origin_{k}"] for k in ("name", "lat", "lon")},
        "destination": {k: request[f"destination_{k}"] for k in ("name", "lat", "lon")},
    }
    origin, destination = (trip["origin"], trip["destination"]) if trip else (theirs["origin"], theirs["destination"])
    departure = _iso(_naive(trip["departure_time"] if trip else request.get("leave_by") or request["requested_time"]))
    if _naive(departure) <= now:
        raise api_error(400, "drive_departed", "This drive would already have left")
    route = matching.route_with_fallback([(origin["lat"], origin["lon"]), (destination["lat"], destination["lon"])])
    price_band = pricing.band(route["distance_m"] / 1000.0)
    price = None
    if price_band:
        price = price_band["default_chf"] if price_chf is None else round(price_chf, 2)
        problem = pricing.check_driver_price(price, price_band)
        if problem:
            raise api_error(400, problem, f"The price per passenger must be between CHF {price_band['min_chf']:.2f}"
                                          f" and CHF {price_band['max_chf']:.2f}")
    planned = {
        "origin_name": origin["name"], "origin_lat": origin["lat"], "origin_lon": origin["lon"],
        "destination_name": destination["name"], "destination_lat": destination["lat"],
        "destination_lon": destination["lon"], "departure_time": departure, "seats_available": seats_available,
        "route_geometry": route["geometry"], "route_distance_m": route["distance_m"],
        "route_duration_s": route["duration_s"],
    }
    if trip:
        # The driver's own trip must serve the passenger as they asked: near
        # both ends and around their time.
        m = matching.match_drive(
            planned, (theirs["origin"]["lat"], theirs["origin"]["lon"]),
            (theirs["destination"]["lat"], theirs["destination"]["lon"]), request["requested_time"],
            request["pickup_radius_km"], request["dropoff_radius_km"], request["time_tolerance_min"],
            time_kind=notify.time_kind(request),
        )
        if m is None:
            raise api_error(400, "not_on_route", "This drive doesn't serve the request: too far from it, or at another time")
        ride = {**theirs, "pickup": m["pickup"], "dropoff": m["dropoff"], "ride_km": m["ride_km"]}
    else:
        ride = _ride(planned, None, None)

    # The first driver to get here takes the request.
    with db.get_conn() as conn:
        taken = conn.execute(
            "UPDATE open_requests SET status = 'accepted' WHERE id = ? AND status = 'open'", (request["id"],)
        )
        if taken.rowcount != 1:
            raise _request_not_open()
    try:
        drive_id = db.create_drive(
            driver_name=driver["name"], seats_available=seats_available, route_geometry=route["geometry"],
            route_distance_m=route["distance_m"], route_duration_s=route["duration_s"], route_source=route["source"],
            driver_price_chf=price, **{k: planned[k] for k in (
                "origin_name", "origin_lat", "origin_lon", "destination_name", "destination_lat", "destination_lon",
                "departure_time",
            )},
        )
        with db.get_conn() as conn:
            booking_id = _insert(
                conn, drive_id, request["passenger_id"], request["passenger_name"], ride, 1,
                driver_note=note, accepted=True,
            )
            conn.execute("UPDATE drives SET seats_available = seats_available - 1 WHERE id = ?", (drive_id,))
    except Exception:
        db.set_request_status(request["id"], "open")  # nothing came of it: others can still take it
        raise

    booking, drive = _get(booking_id)
    notify.send(request["passenger_name"], "request_accepted", _details(booking, drive), request_id=request["id"])
    notify.tell_passengers(db.get_drive(drive_id))
    return _view(booking_id)


# ---------------------------------------------------------------------
# When a drive or an account goes
# ---------------------------------------------------------------------
def tell_drive_cancelled(drive: dict, now: datetime | None = None) -> set[str]:
    """An upcoming drive is being cancelled (by its driver, or because they
    delete their account): tell the passengers with a request or a seat on
    it. Their bookings are deleted with the drive. Returns their names, for
    notify.tell_drive_cancelled to leave out."""
    now = now or notify.local_now()
    if _naive(drive["departure_time"]) <= now:
        return set()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM bookings WHERE drive_id = ? AND status IN ({_in(ACTIVE)})", (drive["id"], *ACTIVE)
        )]
    for booking in rows:
        notify.send(booking["passenger_name"], "booking_drive_cancelled", _details(booking, drive, was=booking["status"]))
    return {booking["passenger_name"] for booking in rows}


def tell_account_deleted(user: dict, now: datetime | None = None) -> int:
    """A user is deleting their account: tell the drivers of the upcoming
    drives they asked for or had a seat on (database.delete_account then
    deletes the bookings and frees the seats). Returns how many."""
    now = now or notify.local_now()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"""
            SELECT b.* FROM bookings b JOIN drives d ON d.id = b.drive_id
            WHERE b.passenger_id = ? AND b.status IN ({_in(ACTIVE)}) AND d.departure_time > ?
            """,
            (user["id"], *ACTIVE, _iso(now)),
        )]
        drives = _drives(conn, sorted({r["drive_id"] for r in rows}))
    for booking in rows:
        drive = drives[booking["drive_id"]]
        notify.send(
            drive["driver_name"], "booking_cancelled",
            _details(booking, drive, by="passenger", was=booking["status"]),
        )
    return len(rows)
