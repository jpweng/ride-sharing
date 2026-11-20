"""
A ride for part of the way and public transport for the rest: what a search
proposes next to the drives that make the whole trip (routes/match.py).

Two shapes, for a passenger going from A to B:

  "ride_then_transit": a drive picks them up near A (within a search's
      radius) and takes them towards B; they get out where its route is
      closest to B, and go on by public transport from the stop there.
  "transit_then_ride": public transport takes them from A to a stop on a
      drive's route, where the drive picks them up and drops them near B.

A drive that passes near both ends is a plain match (search.py), not one of
these. And the car has to do a real part of the way: it brings the passenger
at least MIN_PROGRESS_KM and MIN_PROGRESS_SHARE of the straight line closer
(or starts that much closer, for the second shape).

How it is found:
  1. the drives of the time window that can pass near one end and far
     enough towards the other, from their two ends alone (as search.py does);
  2. on the routes of the best of those, where the passenger gets in and
     out, and when (plain geometry, as matching.py);
  3. for the best MAX_EVALUATED of each shape, the stop at the far point
     and a connection that fits the car's time, from the timetable service
     (transit.py), TIMETABLE_WORKERS at a time and within BUDGET_S: a
     search never waits longer for them.

The time is the passenger's "leave at" or "arrive by" (matching.py's
time_kind), applied to the whole trip: the first thing they board leaves
around their time, or the last thing they ride arrives by it.

A combined trip that takes much longer than public transport alone
(MAX_SLOWER_THAN_TRANSIT) is left out. Each one returned is the drive, as a
match (pickup, dropoff, ride_km, prices for the part ridden), with "shape",
"transit" ({from_station, to_station, connection, fare}), "change" (the
stop where they change: {name, lat, lon}), "starts", "arrives" and "total_min".
"""
import logging
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta

import database as db
import demo_drives
import fares
import matching
import search
import transit
import transit_legs

logger = logging.getLogger("uvicorn.error")

RIDE_FIRST = "ride_then_transit"
TRANSIT_FIRST = "transit_then_ride"

# The car's part: how much closer to the destination it brings the passenger
# (for transit_then_ride: how much of the way is left when they get in).
MIN_PROGRESS_KM = 10.0
MIN_PROGRESS_SHARE = 0.15

# Routes are loaded for this many drives at most, the closest in time.
MAX_ROUTES_LOADED = 80
# Timetable lookups: for this many trips of each shape, at most
# MAX_PER_TRANSFER of them changing at the same place.
MAX_EVALUATED = 3
MAX_PER_TRANSFER = 2
MAX_RESULTS = 4
TIMETABLE_WORKERS = 4
BUDGET_S = 5.0

# Between the car and the stop, either way (as for the legs of a match).
TRANSFER_MIN = transit_legs.TRANSFER_MIN
MAX_STOP_KM = transit_legs.MAX_STOP_KM
# Waiting at the change included, a connection that starts later than this
# after the car arrives (or ends earlier before it leaves) doesn't fit it.
MAX_WAIT_MIN = 60

# Public transport for the far part, estimated before asking the timetable:
# minutes per km of straight line.
TRANSIT_MIN_PER_KM = 1.0

# Left out: a combined trip longer than this many times public transport
# alone, plus a grace.
MAX_SLOWER_THAN_TRANSIT = 1.25
SLOWER_GRACE_MIN = 15

# "Leave at": the trip may start this much before the time asked for, not
# more (nobody leaves an hour earlier than they said they could).
EARLY_START_MIN = 15
# The start and the end of a candidate are estimates until the timetable is
# asked: this much is allowed around the limits before that.
ESTIMATE_SLACK_MIN = 60

_pool = ThreadPoolExecutor(max_workers=TIMETABLE_WORKERS, thread_name_prefix="combined-trips")


def _naive(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _total_min(starts: datetime, arrives: datetime) -> float:
    """How long a trip takes, between its two times as they are shown (to the minute)."""
    return (arrives.replace(second=0, microsecond=0) - starts.replace(second=0, microsecond=0)).total_seconds() / 60.0


def _needed_progress_km(trip_km: float) -> float:
    return max(MIN_PROGRESS_KM, MIN_PROGRESS_SHARE * trip_km)


def _nearest_before(route: list, point: tuple, to_km: float) -> dict | None:
    """matching.nearest_on_route() for the part of `route` up to `to_km`
    along it: the same search on the route read backwards."""
    if to_km <= 0:
        return None
    total_km = route[-1][2]
    backwards = [(lat, lon, total_km - km) for lat, lon, km in reversed(route)]
    found = matching.nearest_on_route(backwards, point, from_km=total_km - to_km)
    if found is not None:
        found["route_km"] = total_km - found["route_km"]
    return found


def car_part(drive: dict, route: list, origin: tuple, destination: tuple, shape: str,
             radius_km: float = search.SEARCH_RADIUS_KM) -> dict | None:
    """
    Where the passenger gets into `drive` and out of it for a combined trip
    of `shape`, or None when the drive doesn't make one: {pickup, dropoff
    ({lat, lon, gap_km, route_km, time}), ride_km, far_gap_km}. "gap_km" is
    the distance to the passenger's own start (pickup) or destination
    (dropoff); far_gap_km is the one public transport covers.
    """
    trip_km = matching.haversine_km(*origin, *destination)
    if shape == RIDE_FIRST:
        pickup = matching.nearest_on_route(route, origin)
        if pickup is None or pickup["gap_km"] > radius_km:
            return None
        dropoff = matching.nearest_on_route(route, destination, from_km=pickup["route_km"] + matching.MIN_RIDE_KM)
        if dropoff is None or dropoff["gap_km"] <= radius_km:
            return None  # none after the pickup, or a plain match
        far_gap_km = dropoff["gap_km"]
    else:
        dropoff = matching.nearest_on_route(route, destination)
        if dropoff is None or dropoff["gap_km"] > radius_km:
            return None
        pickup = _nearest_before(route, origin, dropoff["route_km"] - matching.MIN_RIDE_KM)
        if pickup is None or pickup["gap_km"] <= radius_km:
            return None
        far_gap_km = pickup["gap_km"]
    if trip_km - far_gap_km < _needed_progress_km(trip_km):
        return None

    total_km = route[-1][2]
    duration_s = drive.get("route_duration_s") or total_km / matching.FALLBACK_SPEED_KMH * 3600.0
    departure = _naive(drive["departure_time"])

    def time_at(route_km: float) -> datetime:
        return departure + timedelta(seconds=duration_s * (route_km / total_km if total_km > 0 else 0.0))

    pickup["time"], dropoff["time"] = time_at(pickup["route_km"]), time_at(dropoff["route_km"])
    return {
        "pickup": pickup, "dropoff": dropoff, "far_gap_km": far_gap_km,
        "ride_km": dropoff["route_km"] - pickup["route_km"],
    }


def _could_make(drive: dict, origin: tuple, destination: tuple, shape: str, radius_km: float) -> bool:
    """From the drive's two ends alone: whether its route can pass near one
    end of the trip and far enough towards the other (matching.could_pass_near)."""
    trip_km = matching.haversine_km(*origin, *destination)
    reach_km = trip_km - _needed_progress_km(trip_km)
    if reach_km <= radius_km:
        return False  # too short a trip for the car to do a part of it only
    near, far = (origin, destination) if shape == RIDE_FIRST else (destination, origin)
    return matching.could_pass_near(drive, near, radius_km) and matching.could_pass_near(drive, far, reach_km)


def _estimate(part: dict, shape: str) -> tuple[datetime, datetime]:
    """When the whole trip would start and end, before asking the timetable."""
    far = timedelta(minutes=TRANSFER_MIN + part["far_gap_km"] * TRANSIT_MIN_PER_KM)
    if shape == RIDE_FIRST:
        return part["pickup"]["time"], part["dropoff"]["time"] + far
    return part["pickup"]["time"] - far, part["dropoff"]["time"]


def _off_by_min(starts: datetime, arrives: datetime, asked: datetime, time_kind: str,
                tolerance_min: float, slack_min: float = 0.0) -> float | None:
    """Minutes between the trip and the time asked for, or None when it
    isn't in time. For a time to leave at: the start, from EARLY_START_MIN
    before it to the tolerance after. For a time to arrive by: the arrival,
    never after it, up to the tolerance before. `slack_min` widens both
    limits, for times that are still estimates."""
    if time_kind == "arrive":
        off = (arrives - asked).total_seconds() / 60.0
        return off if -(tolerance_min + slack_min) <= off < 1.0 + slack_min else None
    off = (starts - asked).total_seconds() / 60.0
    return off if -(EARLY_START_MIN + slack_min) <= off <= tolerance_min + slack_min else None


def _candidates(origin: tuple, destination: tuple, asked: datetime, time_kind: str,
                tolerance_min: float, radius_km: float, exclude_ids: set) -> list[dict]:
    """The drives that can make a combined trip around the time asked for,
    with their car part, the most promising first."""
    # Public transport may take a while before the car, or after it.
    slack = timedelta(minutes=tolerance_min + 1) + timedelta(hours=matching.MAX_DRIVE_HOURS)
    drives = db.list_drives(
        upcoming_only=True, with_geometry=False,
        departing_from=_iso(asked - slack - timedelta(hours=matching.MAX_DRIVE_HOURS)),
        departing_until=_iso(asked + slack),
        include_demo=demo_drives.enabled(),
    )
    possible = []
    for d in drives:
        if d["seats_available"] <= 0 or d["id"] in exclude_ids:
            continue
        shapes = [s for s in (RIDE_FIRST, TRANSIT_FIRST) if _could_make(d, origin, destination, s, radius_km)]
        if shapes:
            possible.append((abs((_naive(d["departure_time"]) - asked).total_seconds()), d, shapes))
    possible.sort(key=lambda item: item[0])
    possible = possible[:MAX_ROUTES_LOADED]

    geometries = db.route_geometries([d["id"] for _, d, _ in possible])
    prepared = {}
    found = []
    for _, d, shapes in possible:
        geometry = geometries.get(d["id"])
        if geometry and id(geometry) not in prepared:
            prepared[id(geometry)] = matching.prepare_route(geometry, d)
        route = prepared[id(geometry)] if geometry else matching.prepare_route(None, d)
        for shape in shapes:
            part = car_part(d, route, origin, destination, shape, radius_km)
            if part is None:
                continue
            starts, arrives = _estimate(part, shape)
            off = _off_by_min(starts, arrives, asked, time_kind, tolerance_min, ESTIMATE_SLACK_MIN)
            if off is None:
                continue
            near_gap = part["pickup" if shape == RIDE_FIRST else "dropoff"]["gap_km"]
            # Close to the time, close to the passenger, and the car doing much of the way.
            rank = abs(off) + matching.MINUTES_PER_GAP_KM * near_gap + part["far_gap_km"] * TRANSIT_MIN_PER_KM
            found.append({"drive": d, "route": route, "shape": shape, "part": part, "rank": rank})
    found.sort(key=lambda c: (c["rank"], bool(c["drive"].get("is_demo"))))
    return found


def _pick(candidates: list[dict], shape: str) -> list[dict]:
    """The few of a shape worth asking the timetable about: not all changing
    at the same place."""
    picked, per_place = [], {}
    for c in candidates:
        if c["shape"] != shape:
            continue
        far = c["part"]["dropoff" if shape == RIDE_FIRST else "pickup"]
        place = (round(far["lat"], 2), round(far["lon"], 2))  # about a km
        if per_place.get(place, 0) >= MAX_PER_TRANSFER:
            continue
        per_place[place] = per_place.get(place, 0) + 1
        picked.append(c)
        if len(picked) >= MAX_EVALUATED:
            break
    return picked


def _connection(candidate: dict, end_station: dict) -> dict | None:
    """The public transport part of a candidate: {from_station, to_station,
    connection, fare, starts, arrives}, or None when no stop is near the far
    point or no connection fits the car's time. `end_station`: the stop of
    the passenger's own end that public transport serves."""
    shape, part = candidate["shape"], candidate["part"]
    far = part["dropoff" if shape == RIDE_FIRST else "pickup"]
    stop = transit.nearest_named_station(far["lat"], far["lon"])
    if not stop or not stop.get("name") or (stop.get("distance_m") or 0.0) / 1000.0 > MAX_STOP_KM:
        return None
    if stop["name"] == end_station["name"]:
        return None
    change = timedelta(minutes=TRANSFER_MIN)
    if shape == RIDE_FIRST:
        ready = far["time"] + change
        connections = transit.get_connections(stop["name"], end_station["name"], _iso(ready))
        fitting = [
            c for c in connections
            if ready <= _naive(c["departure"]) <= ready + timedelta(minutes=MAX_WAIT_MIN)
        ]
        if not fitting:
            return None
        best = min(fitting, key=lambda c: _naive(c["arrival"]))
        stations = (stop, end_station)
        starts, arrives = part["pickup"]["time"], _naive(best["arrival"])
    else:
        by = far["time"] - change
        connections = transit.get_connections(end_station["name"], stop["name"], _iso(by), arrive_by=True)
        fitting = [
            c for c in connections
            if by - timedelta(minutes=MAX_WAIT_MIN) <= _naive(c["arrival"]) <= by
        ]
        if not fitting:
            return None
        best = max(fitting, key=lambda c: _naive(c["departure"]))
        stations = (end_station, stop)
        starts, arrives = _naive(best["departure"]), part["dropoff"]["time"]
    return {
        "from_station": stations[0]["name"], "to_station": stations[1]["name"], "stop": stop,
        "connection": best, "fare": fares.fare_between_stations(stations[0], stations[1], best["departure"]),
        "starts": starts, "arrives": arrives,
    }


def _transit_alone_min(transit_option: dict | None) -> float | None:
    """The quickest of the public transport connections for the whole trip, in minutes."""
    durations = []
    for c in (transit_option or {}).get("connections", []):
        try:
            durations.append((_naive(c["arrival"]) - _naive(c["departure"])).total_seconds() / 60.0)
        except (KeyError, TypeError, ValueError):
            continue
    return min(durations) if durations else None


def _as_match(candidate: dict, leg: dict, off_min: float) -> dict:
    """A combined trip as a match (matching.match_drive's shape), with its
    public transport part."""
    d, part, shape = candidate["drive"], candidate["part"], candidate["shape"]

    def point(p: dict) -> dict:
        return {"lat": round(p["lat"], 6), "lon": round(p["lon"], 6), "gap_km": round(p["gap_km"], 1),
                "time": _iso(p["time"]), "name": None}

    pickup, dropoff = point(part["pickup"]), point(part["dropoff"])
    # The far point is at the stop where the passenger changes.
    (dropoff if shape == RIDE_FIRST else pickup)["name"] = leg["stop"]["name"]
    return {
        **d,
        "route_geometry": [[lon, lat] for lat, lon, _ in candidate["route"]],
        "shape": shape,
        "pickup": pickup,
        "dropoff": dropoff,
        "ride_km": round(part["ride_km"], 1),
        "time_diff_min": round(off_min),
        "transit": {k: leg[k] for k in ("from_station", "to_station", "connection", "fare")},
        # Where the passenger changes: the end of their ride that isn't their own.
        "change": {"name": leg["stop"]["name"], "lat": leg["stop"]["lat"], "lon": leg["stop"]["lon"]},
        "starts": _iso(leg["starts"]),
        "arrives": _iso(leg["arrives"]),
        "total_min": round(_total_min(leg["starts"], leg["arrives"])),
    }


def find(origin, destination, requested_time: str, time_kind: str = "depart",
         exclude_ids: set | None = None, transit_option: dict | None = None,
         time_tolerance_min: float = search.SEARCH_TIME_TOLERANCE_MIN,
         radius_km: float = search.SEARCH_RADIUS_KM) -> list[dict]:
    """
    The combined trips for a search from `origin` to `destination` (each
    with .lat, .lon, .name and .kind, as api.LatLon), the best first: for a
    time to leave at, the earliest arrival; for a time to arrive by, the
    latest start. `exclude_ids`: the drives already found as plain matches.
    `transit_option`: public transport for the whole trip (transit.py), to
    leave out what is much slower. Never raises: without the timetable
    service, or anything else going wrong, there are none.
    """
    try:
        return _find(origin, destination, requested_time, time_kind, exclude_ids or set(),
                     transit_option, time_tolerance_min, radius_km)
    except Exception:
        logger.warning("combined trips failed", exc_info=True)
        return []


def _find(origin, destination, requested_time, time_kind, exclude_ids, transit_option,
          time_tolerance_min, radius_km) -> list[dict]:
    asked = _naive(requested_time)
    start, end = (origin.lat, origin.lon), (destination.lat, destination.lon)
    with search._search_slots:  # routes are parsed: one search's worth at a time
        candidates = _candidates(start, end, asked, time_kind, time_tolerance_min, radius_km, exclude_ids)
    picked = {shape: _pick(candidates, shape) for shape in (RIDE_FIRST, TRANSIT_FIRST)}
    if not any(picked.values()):
        return []

    def place_name(point):
        return point.name if point.kind == "place" else None

    # The passenger's own stop at each end, looked up only for a shape that has candidates.
    stations = {}
    if picked[RIDE_FIRST]:
        stations[RIDE_FIRST] = transit.resolve_station(destination.lat, destination.lon, place_name(destination))
    if picked[TRANSIT_FIRST]:
        stations[TRANSIT_FIRST] = transit.resolve_station(origin.lat, origin.lon, place_name(origin))

    futures = {
        _pool.submit(_connection, c, stations[shape]): c
        for shape, chosen in picked.items() if stations.get(shape) for c in chosen
    }
    done, late = wait(futures, timeout=BUDGET_S)
    for future in late:
        future.cancel()

    alone_min = _transit_alone_min(transit_option)
    trips = []
    for future in done:
        try:
            leg = future.result()
        except Exception:
            logger.warning("combined trip lookup failed", exc_info=True)
            continue
        if leg is None:
            continue
        off = _off_by_min(leg["starts"], leg["arrives"], asked, time_kind, time_tolerance_min)
        if off is None:
            continue
        total_min = _total_min(leg["starts"], leg["arrives"])
        if alone_min is not None and total_min > alone_min * MAX_SLOWER_THAN_TRANSIT + SLOWER_GRACE_MIN:
            continue
        trip = _as_match(futures[future], leg, off)
        if alone_min is not None:
            trip["vs_transit_min"] = round(total_min - alone_min)  # negative: quicker than public transport alone
        trips.append(trip)

    # Two drives at the same time on the same trip make one proposal: the real one, if any.
    trips.sort(key=lambda t: bool(t.get("is_demo")))
    unique = {}
    for trip in trips:
        key = (trip["shape"], trip["pickup"]["time"], trip["dropoff"]["time"], trip["change"]["name"],
               trip["transit"]["connection"]["departure"])
        unique.setdefault(key, trip)
    trips = list(unique.values())

    if time_kind == "arrive":
        trips.sort(key=lambda t: (t["starts"], t["arrives"]), reverse=True)
    else:
        trips.sort(key=lambda t: (t["arrives"], t["starts"]))
    return trips[:MAX_RESULTS]
