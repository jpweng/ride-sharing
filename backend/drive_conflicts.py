"""
A driver is in one place at a time: a drive isn't posted when its driver
has another one at the same time, or one so close to it that they couldn't
get from where the earlier ends to where the later starts.

    check(driver_name, origin, destination, departure, duration_s)

raises 409 "drive_overlaps" or "drive_too_close", naming the drive in the
way (the error's "params", for the page's sentence), for a drive from
`origin` to `destination` leaving at `departure` and taking `duration_s`.
Used where a drive is posted: POST /api/drives, and a driver accepting a
passenger's ride request (bookings.accept_request). Drives posted before
this rule stay as they are.

Getting from one drive to the next is a road route like a drive's own
(matching.route_with_fallback: OSRM, else the straight line at 60 km/h),
asked for only when the time between the two could be too short. Only the
drive that ends last before the new one, and the one that starts first
after it, are looked at for that: where the driver is then.

A route's time is the empty road's. The time needed between two drives
(needed_between) adds what a real day adds:
  - usual traffic: driving takes USUAL_TRAFFIC times as long, PEAK_TRAFFIC
    at the weekdays' peak hours (PEAK_HOURS), for the way between the two
    and for the earlier drive itself, which then arrives later than its
    route says;
  - breaks: BREAK_S of rest for every BREAK_EVERY_S at the wheel, the
    earlier drive and the way between counted together.
So even a drive that starts where the one before ends needs a little time
after it: what traffic adds to that drive, and its breaks when it is long.

one_at_a_time(driver_name) keeps two of a driver's drives from being
checked at once (a button pressed twice): the second sees the first.
"""
import threading
from contextlib import contextmanager
from datetime import datetime, time, timedelta

import database as db
import matching
from api import api_error

# The other drives looked at: those leaving from this long before the new
# one to this long after it arrives. Further away in time, there is time.
LOOK_BEFORE_H = 24
LOOK_AFTER_H = 12

# The same place: no way to make between the two drives.
SAME_PLACE_KM = 1.0
# With this much time for the straight line between the two drives, the road
# isn't asked for: slower than any road, with traffic, over any pass.
SURELY_ENOUGH_KMH = 10.0

# Usual traffic: how many times the empty road's time driving takes, at the
# weekdays' peak hours (Monday to Friday, local time) and the rest of the time.
PEAK_HOURS = ((time(6, 30), time(9, 0)), (time(16, 0), time(19, 0)))
PEAK_TRAFFIC = 1.3
USUAL_TRAFFIC = 1.1

# Breaks: this much rest for every so much time at the wheel (the usual
# advice of a quarter of an hour every two hours).
BREAK_EVERY_S = 2 * 3600
BREAK_S = 15 * 60
# Times show to the minute.
GRACE_S = 60

_locks = {}
_locks_guard = threading.Lock()


@contextmanager
def one_at_a_time(driver_name: str):
    """Around checking and posting a drive: one of a driver's at a time."""
    with _locks_guard:
        lock = _locks.setdefault(driver_name, threading.Lock())
    with lock:
        yield


def _naive(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=None)


def _times(drive: dict) -> tuple[datetime, datetime]:
    """When a posted drive leaves and arrives."""
    departs = _naive(drive["departure_time"])
    km = (drive.get("route_distance_m") or 0) / 1000.0
    duration_s = drive.get("route_duration_s") or km / matching.FALLBACK_SPEED_KMH * 3600.0
    return departs, departs + timedelta(seconds=duration_s)


def _traffic(at: datetime) -> tuple[float, datetime]:
    """How many times the empty road's time driving takes at `at`, and until when."""
    if at.weekday() < 5:
        for begins, ends in PEAK_HOURS:
            if at < datetime.combine(at.date(), begins):
                return USUAL_TRAFFIC, datetime.combine(at.date(), begins)
            if at < datetime.combine(at.date(), ends):
                return PEAK_TRAFFIC, datetime.combine(at.date(), ends)
    return USUAL_TRAFFIC, datetime.combine(at.date(), time(0, 0)) + timedelta(days=1)


def with_traffic(leaves: datetime, empty_road_s: float) -> float:
    """Seconds a drive of `empty_road_s` takes in usual traffic, leaving at
    `leaves`: each part of it at the pace of the hour it is driven in."""
    at, left, taken = leaves, float(empty_road_s), 0.0
    while left > 0:
        slower, until = _traffic(at)
        span = (until - at).total_seconds()
        if left * slower <= span:
            return taken + left * slower
        taken, left, at = taken + span, left - span / slower, until
    return taken


def breaks_s(driving_s: float) -> float:
    """The rest that goes with `driving_s` at the wheel."""
    return int(driving_s // BREAK_EVERY_S) * BREAK_S


def needed_between(leaves: datetime, drive_s: float, way_s: float) -> float:
    """
    Seconds needed between the arrival a drive's route gives (it leaves at
    `leaves`, `drive_s` on the empty road) and the start of a next drive
    `way_s` of empty road away: what traffic adds to the drive itself, the
    way in traffic, and the breaks for the two together.
    """
    drive_real_s = with_traffic(leaves, drive_s)
    way_real_s = with_traffic(leaves + timedelta(seconds=drive_real_s), way_s)
    return (drive_real_s - drive_s) + way_real_s + breaks_s(drive_real_s + way_real_s)


def _needed_s(leaves: datetime, drive_s: float, start: tuple, end: tuple, time_s: float) -> float:
    """needed_between() for the way by road from `start` to `end` ((lat,
    lon) each), or 0 when `time_s` is surely enough (the road isn't asked
    for then)."""
    km = matching.haversine_km(*start, *end)
    if km <= SAME_PLACE_KM:
        return needed_between(leaves, drive_s, 0.0)
    # As if the way were driven at SURELY_ENOUGH_KMH on an empty road: more than it can take.
    if time_s >= needed_between(leaves, drive_s, km / SURELY_ENOUGH_KMH * 3600.0 / USUAL_TRAFFIC):
        return 0.0
    return needed_between(leaves, drive_s, matching.route_with_fallback([start, end])["duration_s"])


def _duration(seconds: float) -> str:
    """As the page writes durations: "2 h 15", "45 min"."""
    minutes = max(1, round(seconds / 60.0))
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"


def _named(drive: dict, **more) -> dict:
    """The drive in the way, for the page's sentence."""
    return {"from": drive["origin_name"], "to": drive["destination_name"],
            "time": drive["departure_time"][11:16], **more}


def conflict(driver_name: str, origin: tuple, destination: tuple, departure: datetime, duration_s: float) -> dict | None:
    """
    What keeps `driver_name` from driving from `origin` to `destination`
    ((lat, lon) each) at `departure` for `duration_s`: {"code":
    "drive_overlaps" or "drive_too_close", "drive": theirs in the way,
    "params": for the sentence}; None when nothing does.
    """
    arrival = departure + timedelta(seconds=duration_s)
    others = [(d, *_times(d)) for d in db.list_drives(
        upcoming_only=False, driver_name=driver_name, with_geometry=False,
        departing_from=(departure - timedelta(hours=LOOK_BEFORE_H)).isoformat(timespec="minutes"),
        departing_until=(arrival + timedelta(hours=LOOK_AFTER_H)).isoformat(timespec="minutes"),
    )]
    for other, leaves, arrives in others:
        if leaves < arrival and departure < arrives:
            return {"code": "drive_overlaps", "drive": other, "params": _named(other)}

    # Where the driver is before and after: the drive that ends last before
    # this one, and the one that starts first after it.
    before = max((o for o in others if o[2] <= departure), key=lambda o: o[2], default=None)
    after = min((o for o in others if o[1] >= arrival), key=lambda o: o[1], default=None)
    # Each as (their drive, when the earlier of the two leaves, its time on
    # the empty road, where it ends, where the later one starts, the time
    # between the two).
    ways = []
    if before:
        theirs, leaves, arrives = before
        ends = (theirs["destination_lat"], theirs["destination_lon"])
        ways.append((theirs, leaves, (arrives - leaves).total_seconds(), ends, origin, (departure - arrives).total_seconds()))
    if after:
        theirs, leaves, _ = after
        starts = (theirs["origin_lat"], theirs["origin_lon"])
        ways.append((theirs, departure, duration_s, destination, starts, (leaves - arrival).total_seconds()))
    for other, leaves, drive_s, start, end, time_s in ways:
        needed_s = _needed_s(leaves, drive_s, start, end, time_s)
        if needed_s > time_s + GRACE_S:
            return {"code": "drive_too_close", "drive": other, "params": _named(other, duration=_duration(needed_s))}
    return None


def check(driver_name: str, origin: tuple, destination: tuple, departure: datetime, duration_s: float) -> None:
    """Raises 409 "drive_overlaps" or "drive_too_close" when conflict() finds one."""
    found = conflict(driver_name, origin, destination, departure, duration_s)
    if found is None:
        return
    other, params = found["drive"], found["params"]
    if found["code"] == "drive_overlaps":
        message = f"You already have a drive at that time: {params['from']} to {params['to']}, leaving at {params['time']}"
    else:
        message = (f"Not enough time between this drive and yours from {params['from']} to {params['to']} at "
                   f"{params['time']}: about {params['duration']} are needed between the two, for the way in usual "
                   f"traffic and for breaks")
    raise api_error(409, found["code"], message, params={**params, "drive_id": other["id"]})
