"""
Ratings: after a ride, the passenger rates the driver and the driver the
passenger, 1 to 5 stars. See docs/ratings-and-reports.md.

  - Who: the two sides of an accepted booking, once the ride should be over
    (the booking's drop-off time), each once. No comment: words about a
    problem go in a report (reports.py), which only the team reads.
  - Asking: the hourly maintenance run sends both a "rate_ride"
    notification once the ride is over (ask_after_rides); the stars are
    given there. A booking is deleted with its drive, 30 days after the
    departure: until then it can be rated.
  - Showing: a person's average and number of ratings, separately as a
    driver and as a passenger (summary): next to the driver's name on
    drives and search results, and next to the passenger's on the driver's
    requests. Nobody sees who gave which rating.
  - Ratings outlive the booking and the drive. They go when the rated
    person's account goes; those a person gave stay, without their author
    (the migration's foreign keys).
"""
import sqlite3
from datetime import datetime

import database as db
import notify
from api import api_error

MIN_STARS, MAX_STARS = 1, 5
ROLES = ("driver", "passenger")


def _in(values) -> str:
    return ",".join("?" * len(values))


def summary(names: list[str], role: str) -> dict[str, dict]:
    """name -> {"average": to one decimal, "count"}: what the people with
    these (unique) names were rated as drivers or as passengers. Those
    without ratings are left out."""
    names = sorted(set(names))
    found = {}
    with db.get_conn() as conn:
        for i in range(0, len(names), 500):  # stay under SQLite's variable limit
            chunk = names[i:i + 500]
            rows = conn.execute(
                f"""
                SELECT u.name, AVG(r.stars) AS average, COUNT(*) AS count
                FROM ratings r JOIN users u ON u.id = r.rated_id
                WHERE r.rated_role = ? AND u.name IN ({_in(chunk)}) GROUP BY u.id
                """,
                (role, *chunk),
            )
            found.update({r["name"]: {"average": round(r["average"], 1), "count": r["count"]} for r in rows})
    return found


def with_driver_ratings(drives: list[dict]) -> list[dict]:
    """The drives (or matches), each with driver_rating ({average, count},
    or None without ratings yet). Demo drivers have none."""
    rated = summary([d["driver_name"] for d in drives if not d.get("is_demo")], "driver")
    for d in drives:
        d["driver_rating"] = rated.get(d["driver_name"])
    return drives


def with_booking_ratings(bookings_by_side: dict) -> dict:
    """bookings.for_user()'s answer, each booking with passenger_rating and
    its drive with driver_rating."""
    views = [b for side in bookings_by_side.values() for b in side]
    passengers = summary([b["passenger_name"] for b in views], "passenger")
    drivers = summary([b["drive"]["driver_name"] for b in views], "driver")
    for b in views:
        b["passenger_rating"] = passengers.get(b["passenger_name"])
        b["drive"]["driver_rating"] = drivers.get(b["drive"]["driver_name"])
    return bookings_by_side


def _booking(booking_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT b.*, d.driver_id, d.driver_name, d.origin_name AS drive_origin_name,
                   d.destination_name AS drive_destination_name, d.departure_time
            FROM bookings b JOIN drives d ON d.id = b.drive_id WHERE b.id = ?
            """,
            (booking_id,),
        ).fetchone()
        return dict(row) if row else None


def sides(booking_id: int, user: dict) -> tuple[dict, str]:
    """(the booking with its drive's driver, the role of the OTHER side) for
    a user who is its passenger or its driver; 404 for anyone else."""
    booking = _booking(booking_id)
    if booking and booking["passenger_id"] == user["id"]:
        return booking, "driver"
    if booking and booking["driver_id"] == user["id"]:
        return booking, "passenger"
    # Also for a booking the user has no part in: whether an id exists is
    # nobody else's business.
    raise api_error(404, "booking_not_found", "Booking not found")


def rate(booking_id: int, rater: dict, stars: int, now: datetime | None = None) -> dict:
    """`rater` gives the other side of the booking `stars`; returns
    {"rated_role", "stars"}."""
    now = now or notify.local_now()
    booking, rated_role = sides(booking_id, rater)
    if booking["status"] != "accepted":
        raise api_error(400, "not_rateable", "Only a ride that was booked can be rated")
    if datetime.fromisoformat(booking["dropoff_time"]).replace(tzinfo=None) > now:
        raise api_error(400, "ride_not_over", "This ride isn't over yet")
    rated_id = booking["driver_id"] if rated_role == "driver" else booking["passenger_id"]
    try:
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO ratings (booking_id, rater_id, rated_id, rated_role, stars, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (booking_id, rater["id"], rated_id, rated_role, stars, db.now_iso()),
            )
    except sqlite3.IntegrityError:
        raise api_error(400, "already_rated", "You have already rated this ride")
    return {"rated_role": rated_role, "stars": stars}


def ask_after_rides(now: datetime | None = None) -> int:
    """Once per booking: when an accepted booking's ride is over, ask its
    passenger how the driver was and its driver how the passenger was
    (a "rate_ride" notification each). Returns how many bookings."""
    now = now or notify.local_now()
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT b.id, b.passenger_name, b.dropoff_time, d.driver_name, d.origin_name, d.destination_name,
                   d.departure_time, d.id AS drive_id
            FROM bookings b JOIN drives d ON d.id = b.drive_id
            WHERE b.status = 'accepted' AND b.rating_asked_at IS NULL AND b.dropoff_time <= ?
            """,
            (now.isoformat(timespec="minutes"),),
        )]
        conn.executemany(
            "UPDATE bookings SET rating_asked_at = ? WHERE id = ?", [(db.now_iso(), r["id"]) for r in rows]
        )
    for r in rows:
        drive = {k: r[k] for k in ("driver_name", "origin_name", "destination_name", "departure_time")}
        drive["id"] = r["drive_id"]
        for recipient, rated_role, rated_name in (
            (r["passenger_name"], "driver", r["driver_name"]),
            (r["driver_name"], "passenger", r["passenger_name"]),
        ):
            notify.send(recipient, "rate_ride", {
                "booking_id": r["id"], "rated_role": rated_role, "rated_name": rated_name, "drive": drive,
            })
    return len(rows)
