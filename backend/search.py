"""
Upcoming drives that fit a passenger's trip: loads the drives that can
reach the pickup in the time window, and their routes only for those that
can pass near both ends, then matches them (see matching.py). Used by
/api/match and by ride requests (notify.py).

A search also proposes a few rides just outside what the passenger
selected (with_outside): within wider circles and a wider time window,
each marked with what it's outside of. The passenger may like them anyway.
"""
from datetime import datetime, timedelta

import database as db
import matching

# The wider limits for rides outside the passenger's selection: twice their
# circles and time flexibility, and at least 5 km / 1 h more.
WIDER_FACTOR = 2.0
WIDER_MIN_EXTRA_KM = 5.0
WIDER_MIN_EXTRA_MIN = 60
MAX_OUTSIDE_RESULTS = 5


def upcoming_matches(
    origin: tuple[float, float],
    destination: tuple[float, float],
    requested_time: str,
    pickup_radius_km: float = matching.DEFAULT_PICKUP_RADIUS_KM,
    dropoff_radius_km: float = matching.DEFAULT_DROPOFF_RADIUS_KM,
    time_tolerance_min: float = matching.DEFAULT_TIME_TOLERANCE_MIN,
    max_results: int = matching.MAX_RESULTS,
) -> list[dict]:
    # Only drives that can reach the pickup near the requested time
    # (matching filters on the pickup time exactly): there are tens of
    # thousands of demo drives a week.
    asked = datetime.fromisoformat(requested_time).replace(tzinfo=None)
    tolerance = timedelta(minutes=time_tolerance_min + 1)
    drives = db.list_drives(
        upcoming_only=True,
        with_geometry=False,
        departing_from=(asked - tolerance - timedelta(hours=matching.MAX_DRIVE_HOURS)).isoformat(timespec="minutes"),
        departing_until=(asked + tolerance).isoformat(timespec="minutes"),
    )
    # Routes (large) only for the drives that can pass near both ends.
    candidates = [
        d for d in drives
        if d["seats_available"] > 0
        and matching.could_pass_near(d, origin, pickup_radius_km)
        and matching.could_pass_near(d, destination, dropoff_radius_km)
    ]
    geometries = db.route_geometries([d["id"] for d in candidates])
    for d in candidates:
        d["route_geometry"] = geometries.get(d["id"])
    return matching.find_matches(
        candidates, origin, destination, requested_time,
        pickup_radius_km=pickup_radius_km,
        dropoff_radius_km=dropoff_radius_km,
        time_tolerance_min=time_tolerance_min,
        max_results=max_results,
    )


def with_outside(
    origin: tuple[float, float],
    destination: tuple[float, float],
    requested_time: str,
    pickup_radius_km: float = matching.DEFAULT_PICKUP_RADIUS_KM,
    dropoff_radius_km: float = matching.DEFAULT_DROPOFF_RADIUS_KM,
    time_tolerance_min: float = matching.DEFAULT_TIME_TOLERANCE_MIN,
) -> tuple[list[dict], list[dict]]:
    """
    (matches, outside): the matches within the passenger's selection, as
    upcoming_matches gives them, and up to MAX_OUTSIDE_RESULTS more within
    the wider limits, each with "outside": what it's outside of ("pickup",
    "dropoff", "time"). One search with the wider limits, split: a drive's
    pickup and drop-off points don't depend on the radii.
    """
    def wider_km(km):
        return max(km * WIDER_FACTOR, km + WIDER_MIN_EXTRA_KM)

    found = upcoming_matches(
        origin, destination, requested_time,
        wider_km(pickup_radius_km), wider_km(dropoff_radius_km),
        max(time_tolerance_min * WIDER_FACTOR, time_tolerance_min + WIDER_MIN_EXTRA_MIN),
        max_results=10**6,
    )
    matches, outside = [], []
    for m in found:
        # On the rounded figures the passenger sees, so "5.0 km" is never
        # outside a 5 km circle.
        m["outside"] = [
            limit for limit, out in (
                ("pickup", m["pickup"]["gap_km"] > pickup_radius_km),
                ("dropoff", m["dropoff"]["gap_km"] > dropoff_radius_km),
                ("time", abs(m["time_diff_min"]) > time_tolerance_min),
            ) if out
        ]
        (outside if m["outside"] else matches).append(m)
    return matches[:matching.MAX_RESULTS], outside[:MAX_OUTSIDE_RESULTS]
