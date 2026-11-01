"""
Upcoming drives that fit a passenger's trip: loads the drives that can
reach the pickup in the time window, and their routes only for those that
can pass near both ends, then matches them (see matching.py). Used by
/api/match and by ride requests (notify.py).
"""
from datetime import datetime, timedelta

import database as db
import matching


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
