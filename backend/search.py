"""
Upcoming drives that fit a passenger's trip: loads the drives that can
reach the pickup in the time window, and their routes only for those that
can pass near both ends, then matches them (see matching.py). Used by
/api/match and by ride requests (notify.py).

The passenger gives a start, a destination and a time, nothing else: how
far a drive may pass from each end and how far from their time it may be
are fixed here (SEARCH_RADIUS_KM, SEARCH_TIME_TOLERANCE_MIN), wide enough
to show what there is. The results are one list, the best first: the
closest to their time and to both ends (matching.py's score).

When no drive that can be booked passes that near, a search looks further
(matches_for_search): the bookable drives passing within WIDER_RADIUS_KM
of both ends join the list, marked "wider". The rest of the way is the
passenger's to cover; transit_legs.py proposes public transport for it.

Every drive returned says whether it can be booked ("bookable"): demo
drives can't, they're fictional. With demo drives off (demo_drives.enabled),
they're left out altogether.
"""
import os
import threading
from datetime import datetime, timedelta

import database as db
import demo_drives
import matching

# Searches running at once. A search is Python work (one CPU at a time, the
# GIL) and holds ~30-50 MB of parsed routes while it runs. In the load test
# (loadtest/), 40 at once took the app to 1.6 GB, and even two at once cost
# twice the CPU each (their threads keep taking the GIL from each other
# around SQLite calls). One at a time, the others wait their turn, ~0.2 s
# each, and the server does the most searches per second.
SEARCH_CONCURRENCY = int(os.environ.get("SEARCH_CONCURRENCY", "1"))
_search_slots = threading.BoundedSemaphore(SEARCH_CONCURRENCY)


def with_bookable(drive: dict) -> dict:
    """The drive, marked as bookable unless it's a fictional demo drive."""
    drive["bookable"] = not drive.get("is_demo")
    return drive


# What a search covers: drives whose route passes within this distance of
# the passenger's start and of their destination, with a pickup within this
# long of their time. Ride requests keep the same limits.
SEARCH_RADIUS_KM = 10.0
SEARCH_TIME_TOLERANCE_MIN = 240

# How far a search looks when nothing bookable passes within
# SEARCH_RADIUS_KM: a drive worth a train or bus ride to its pickup. Further
# than this, the way to the car is a trip of its own. (A match's two gaps
# together must also stay shorter than the trip itself, see matching.py.)
WIDER_RADIUS_KM = 30.0


def upcoming_matches(
    origin: tuple[float, float],
    destination: tuple[float, float],
    requested_time: str,
    pickup_radius_km: float = SEARCH_RADIUS_KM,
    dropoff_radius_km: float = SEARCH_RADIUS_KM,
    time_tolerance_min: float = SEARCH_TIME_TOLERANCE_MIN,
    max_results: int = matching.MAX_RESULTS,
    include_demo: bool = True,
) -> list[dict]:
    """The upcoming drives that serve the trip, the best first; by default
    within a search's fixed limits (a ride request passes its own, kept
    from when it was made)."""
    with _search_slots:
        return _upcoming_matches(
            origin, destination, requested_time,
            pickup_radius_km, dropoff_radius_km, time_tolerance_min, max_results, include_demo,
        )


def matches_for_search(
    origin: tuple[float, float],
    destination: tuple[float, float],
    requested_time: str,
) -> list[dict]:
    """A passenger's search: the drives within SEARCH_RADIUS_KM, the best
    first, each marked "wider": False. When none of them can be booked, the
    bookable drives passing further away, within WIDER_RADIUS_KM, are added
    ("wider": True), in the same order of merit."""
    matches = upcoming_matches(origin, destination, requested_time)
    for m in matches:
        m["wider"] = False
    if any(m["bookable"] for m in matches):
        return matches
    # Demo drives are left out of the wider look: there are thousands, and
    # a fictional drive 25 km away helps nobody.
    wider = upcoming_matches(
        origin, destination, requested_time,
        pickup_radius_km=WIDER_RADIUS_KM, dropoff_radius_km=WIDER_RADIUS_KM, include_demo=False,
    )
    for m in wider:
        m["wider"] = True
    matches = sorted(matches + wider, key=matching.merit)
    return matches[:matching.MAX_RESULTS]


def _upcoming_matches(
    origin, destination, requested_time,
    pickup_radius_km, dropoff_radius_km, time_tolerance_min, max_results, include_demo=True,
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
        include_demo=include_demo and demo_drives.enabled(),
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
    return [with_bookable(m) for m in matching.find_matches(
        candidates, origin, destination, requested_time,
        pickup_radius_km=pickup_radius_km,
        dropoff_radius_km=dropoff_radius_km,
        time_tolerance_min=time_tolerance_min,
        max_results=max_results,
    )]
