"""
A ride for part of the way and public transport for the rest: what a search
proposes next to the drives that make the whole trip (routes/match.py).

Three shapes, for a passenger going from A to B:

  "ride_then_transit": a drive picks them up near A (within a search's
      radius) and takes them towards B; they get out where its route is
      closest to B, and go on by public transport from the stop there.
  "transit_then_ride": public transport takes them from A to a stop on a
      drive's route, where the drive picks them up and drops them near B.
  "transit_ride_transit": a drive that is near neither A nor B but goes
      their way (Zurich to Fribourg, for St. Gallen to a village near
      Payerne). They ride all of it: public transport takes them to where
      it starts, and on from where it ends. Two changes: these are looked
      for only when a search has fewer than MAX_RESULTS drives and combined
      trips to show, the way by the car may not be much of a detour
      (MAX_DETOUR), and they may take longer than the others
      (MAX_SLOWER_BETWEEN).

A drive that passes near both ends is a plain match (search.py), not one of
these. When a search has MAX_RESULTS of those or more, the fictional demo
drives are left out here (as in search.py's wider look): only drives that
can be booked are worth a change next to that many for the whole way, and
the search answers without waiting for the timetable. And the ride has to be worth a change: it lasts at least MIN_RIDE_MIN,
and it goes the passenger's way, bringing them closer to their destination
by at least MIN_CLOSER_SHARE of the km ridden.

How it is found:
  1. the drives of the time window that last long enough and can pass near
     one end, from their two ends alone (as search.py does);
  2. on the routes of those closest in time (MAX_ROUTES_LOADED routes),
     where the passenger gets in and out, and when (plain geometry, as
     matching.py);
  3. the stop at the far point and a connection that fits the car's time,
     from the timetable service (transit.py), TIMETABLE_WORKERS lookups at
     a time and within BUDGET_S: a search never waits longer for them.
     Where the car is at the end of the trip the time is about (a ride
     first to leave at a time, a ride last to arrive by one): for the best
     MAX_EVALUATED drives, a lookup at each one's own time. Where public
     transport is: one lookup at the time asked for serves every drive
     that changes at the same place, for the best MAX_EVALUATED places
     (_legs_at).
  4. when that leaves room, the rides between two public transport legs,
     from the drives' two ends and times alone (_between_candidates), and
     their connections in what is left of BUDGET_S: the leg at the end the
     time is about with one lookup per place, the other at the car's own
     time (_between_at).

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
import time
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
TRANSIT_BOTH = "transit_ride_transit"

# The car's part: a ride shorter than this isn't worth a change. (A drive
# for the whole trip is a plain match, however short.)
MIN_RIDE_MIN = 10
# And it goes the passenger's way: they are closer to their destination when
# they get out than when they got in, by at least this share of the km ridden.
MIN_CLOSER_SHARE = 0.5

# A ride between two public transport legs: the way to the car, the drive and
# the way on from it, in straight lines, are together at most this many
# times the straight line between the passenger's two ends.
MAX_DETOUR = 1.15
# Timetable lookups for them: this many places to change at, at the end of
# the trip the time is about.
MAX_BETWEEN_PLACES = 2

# This many routes are loaded at most, those of the drives closest in time
# (_away_min). Demo drives on the same trip share one route.
MAX_ROUTES_LOADED = 80
# Timetable lookups: for this many trips of each shape (or this many places
# to change at, see _legs_at), at most MAX_PER_TRANSFER trips changing at the
# same place.
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

# A ride between two public transport legs has two changes and is seldom as
# quick: it is shown, with how long it takes, up to this many times public
# transport alone, plus a grace.
MAX_SLOWER_BETWEEN = 1.5
SLOWER_BETWEEN_GRACE_MIN = 30

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
    of `shape`, or None when the drive doesn't make one (it doesn't pass near
    their end, or the ride is too short or not their way): {pickup, dropoff
    ({lat, lon, gap_km, route_km, time}), ride_km, far_gap_km}. "gap_km" is
    the distance to the passenger's own start (pickup) or destination
    (dropoff); far_gap_km is the one public transport covers.
    """
    places = _car_places(route, origin, destination, shape, radius_km)
    return _with_times(places, drive, route) if places else None


def _car_places(route: list, origin: tuple, destination: tuple, shape: str, radius_km: float) -> dict | None:
    """car_part() without the times: the same for every drive on `route`."""
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
    ride_km = dropoff["route_km"] - pickup["route_km"]
    closer_km = (matching.haversine_km(pickup["lat"], pickup["lon"], *destination)
                 - matching.haversine_km(dropoff["lat"], dropoff["lon"], *destination))
    if closer_km < MIN_CLOSER_SHARE * ride_km:
        return None
    return {"pickup": pickup, "dropoff": dropoff, "far_gap_km": far_gap_km, "ride_km": ride_km}


def _with_times(places: dict, drive: dict, route: list) -> dict | None:
    """The car part of `drive` at `places` (_car_places), with the times it
    is at each; None when the ride is shorter than MIN_RIDE_MIN."""
    total_km = route[-1][2]
    duration_s = drive.get("route_duration_s") or total_km / matching.FALLBACK_SPEED_KMH * 3600.0
    departure = _naive(drive["departure_time"])

    def time_at(route_km: float) -> datetime:
        return departure + timedelta(seconds=duration_s * (route_km / total_km if total_km > 0 else 0.0))

    gets_in, gets_out = time_at(places["pickup"]["route_km"]), time_at(places["dropoff"]["route_km"])
    if gets_out - gets_in < timedelta(minutes=MIN_RIDE_MIN):
        return None
    return {
        **places,
        "pickup": {**places["pickup"], "time": gets_in},
        "dropoff": {**places["dropoff"], "time": gets_out},
    }


def _could_make(drive: dict, origin: tuple, destination: tuple, shape: str, radius_km: float) -> bool:
    """From the drive's two ends and its duration alone: whether it lasts
    long enough for a ride and its route can pass near the passenger's end
    of the car part (matching.could_pass_near)."""
    if matching.haversine_km(*origin, *destination) <= radius_km:
        return False  # too short a trip for the car to do a part of it only
    if (drive.get("route_duration_s") or MIN_RIDE_MIN * 60) < MIN_RIDE_MIN * 60:
        return False
    return matching.could_pass_near(drive, origin if shape == RIDE_FIRST else destination, radius_km)


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


def _away_min(drive: dict, shape: str, asked: datetime, time_kind: str, trip_km: float) -> float:
    """
    From the drive's two times alone: minutes between it and the moment the
    passenger would be at the car's end of the trip (their start for a ride
    first, their destination for a ride last); 0 when the car is on the road
    then. The time asked for is about one end of the trip; the other end is
    the whole trip away, counted as public transport (TRANSIT_MIN_PER_KM):
    to arrive by 11:00 after 200 km, a ride first leaves around 07:40.
    """
    whole = timedelta(minutes=trip_km * TRANSIT_MIN_PER_KM)
    if shape == RIDE_FIRST:
        wanted = asked - whole if time_kind == "arrive" else asked
    else:
        wanted = asked if time_kind == "arrive" else asked + whole
    departs = _naive(drive["departure_time"])
    arrives = departs + timedelta(seconds=drive.get("route_duration_s") or 0.0)
    return max((departs - wanted).total_seconds(), (wanted - arrives).total_seconds(), 0.0) / 60.0


def _route_key(drive: dict):
    """What tells one stored route from another before they are loaded: demo
    drives between the same two places share theirs, a posted drive has its own."""
    if drive.get("is_demo"):
        return (drive["origin_lat"], drive["origin_lon"], drive["destination_lat"], drive["destination_lon"])
    return drive["id"]


def _candidates(origin: tuple, destination: tuple, asked: datetime, time_kind: str,
                tolerance_min: float, radius_km: float, exclude_ids: set,
                include_demo: bool = True) -> list[dict]:
    """The drives that can make a combined trip around the time asked for,
    with their car part, the most promising first. `include_demo`: False
    leaves the demo drives out."""
    # Public transport may take a while before the car, or after it.
    slack = timedelta(minutes=tolerance_min + 1) + timedelta(hours=matching.MAX_DRIVE_HOURS)
    drives = db.list_drives(
        upcoming_only=True, with_geometry=False,
        departing_from=_iso(asked - slack - timedelta(hours=matching.MAX_DRIVE_HOURS)),
        departing_until=_iso(asked + slack),
        include_demo=include_demo and demo_drives.enabled(),
    )
    trip_km = matching.haversine_km(*origin, *destination)
    possible = []
    for d in drives:
        if d["seats_available"] <= 0 or d["id"] in exclude_ids:
            continue
        for shape in (RIDE_FIRST, TRANSIT_FIRST):
            if _could_make(d, origin, destination, shape, radius_km):
                possible.append((_away_min(d, shape, asked, time_kind, trip_km), d, shape))
    possible.sort(key=lambda item: (item[0], item[1]["departure_time"], item[1]["id"]))
    # The closest in time, up to MAX_ROUTES_LOADED routes: every drive on them.
    kept, routes = [], set()
    for _, d, shape in possible:
        key = _route_key(d)
        if key not in routes:
            if len(routes) >= MAX_ROUTES_LOADED:
                continue
            routes.add(key)
        kept.append((d, shape))

    geometries = db.route_geometries(list({d["id"] for d, _ in kept}))
    prepared, places = {}, {}
    found = []
    for d, shape in kept:
        geometry = geometries.get(d["id"])
        if geometry:
            # Drives on the same trip share one geometry: where the
            # passenger gets in and out is worked out once for them all.
            if id(geometry) not in prepared:
                prepared[id(geometry)] = matching.prepare_route(geometry, d)
            route = prepared[id(geometry)]
            if (id(geometry), shape) not in places:
                places[(id(geometry), shape)] = _car_places(route, origin, destination, shape, radius_km)
            where = places[(id(geometry), shape)]
        else:
            route = matching.prepare_route(None, d)
            where = _car_places(route, origin, destination, shape, radius_km)
        part = _with_times(where, d, route) if where is not None else None
        if part is not None:
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


def _between_candidates(origin: tuple, destination: tuple, asked: datetime, time_kind: str,
                        tolerance_min: float, radius_km: float, exclude_ids: set) -> list[dict]:
    """
    The drives that can be the middle of a trip around the time asked for,
    public transport taking the passenger to where the drive starts and on
    from where it ends, the most promising first (as _candidates gives the
    others, without their routes: "route" is None). All from a drive's two
    ends and times: it is near neither end of the trip (those are the other
    shapes'), it lasts at least MIN_RIDE_MIN, it goes the passenger's way
    (MIN_CLOSER_SHARE) and it isn't much of a detour (MAX_DETOUR).
    """
    slack = timedelta(minutes=tolerance_min + 1) + timedelta(hours=matching.MAX_DRIVE_HOURS)
    drives = db.list_drives(
        upcoming_only=True, with_geometry=False,
        departing_from=_iso(asked - slack - timedelta(hours=matching.MAX_DRIVE_HOURS)),
        departing_until=_iso(asked + slack),
        include_demo=demo_drives.enabled(),
    )
    trip_km = matching.haversine_km(*origin, *destination)
    found = []
    for d in drives:
        if d["seats_available"] <= 0 or d["id"] in exclude_ids:
            continue
        start, end = (d["origin_lat"], d["origin_lon"]), (d["destination_lat"], d["destination_lon"])
        before_km = matching.haversine_km(*origin, *start)
        if before_km <= radius_km or before_km > MAX_DETOUR * trip_km:
            continue
        after_km = matching.haversine_km(*end, *destination)
        straight_km = matching.haversine_km(*start, *end)
        if after_km <= radius_km or before_km + straight_km + after_km > MAX_DETOUR * trip_km:
            continue
        ride_km = (d.get("route_distance_m") or 0) / 1000.0 or straight_km
        duration_s = d.get("route_duration_s") or ride_km / matching.FALLBACK_SPEED_KMH * 3600.0
        if duration_s < MIN_RIDE_MIN * 60:
            continue
        if matching.haversine_km(*start, *destination) - after_km < MIN_CLOSER_SHARE * ride_km:
            continue
        departs = _naive(d["departure_time"])
        part = {
            "pickup": {"lat": start[0], "lon": start[1], "gap_km": before_km, "route_km": 0.0, "time": departs},
            "dropoff": {"lat": end[0], "lon": end[1], "gap_km": after_km, "route_km": ride_km,
                        "time": departs + timedelta(seconds=duration_s)},
            "far_gap_km": before_km + after_km, "ride_km": ride_km,
        }
        # When the whole trip would start and end, before asking the timetable.
        starts = part["pickup"]["time"] - timedelta(minutes=TRANSFER_MIN + before_km * TRANSIT_MIN_PER_KM)
        arrives = part["dropoff"]["time"] + timedelta(minutes=TRANSFER_MIN + after_km * TRANSIT_MIN_PER_KM)
        off = _off_by_min(starts, arrives, asked, time_kind, tolerance_min, ESTIMATE_SLACK_MIN)
        if off is None:
            continue
        # Close to the time, and the car doing much of the way.
        rank = abs(off) + part["far_gap_km"] * TRANSIT_MIN_PER_KM
        found.append({"drive": d, "route": None, "shape": TRANSIT_BOTH, "part": part, "rank": rank})
    found.sort(key=lambda c: (c["rank"], bool(c["drive"].get("is_demo"))))
    return found


def _far(candidate: dict) -> dict:
    """The end of the car part where the passenger changes."""
    return candidate["part"]["dropoff" if candidate["shape"] == RIDE_FIRST else "pickup"]


def _change_place(candidate: dict) -> tuple:
    """Where the passenger changes, to about a km."""
    far = _far(candidate)
    return (round(far["lat"], 2), round(far["lon"], 2))


def _car_time(candidate: dict) -> datetime:
    """When the car is where the passenger changes, as it is shown (to the minute)."""
    return _far(candidate)["time"].replace(second=0, microsecond=0)


def _pick(candidates: list[dict], shape: str) -> list[dict]:
    """The few of a shape worth asking the timetable about: not all changing
    at the same place."""
    picked, per_place = [], {}
    for c in candidates:
        if c["shape"] != shape:
            continue
        place = _change_place(c)
        if per_place.get(place, 0) >= MAX_PER_TRANSFER:
            continue
        per_place[place] = per_place.get(place, 0) + 1
        picked.append(c)
        if len(picked) >= MAX_EVALUATED:
            break
    return picked


def _by_place(candidates: list[dict], shape: str) -> list[list[dict]]:
    """The candidates of a shape by the place where the passenger changes:
    the MAX_EVALUATED places of the most promising ones, each with all of
    its candidates."""
    places = {}
    for c in candidates:
        if c["shape"] == shape:
            places.setdefault(_change_place(c), []).append(c)
    return list(places.values())[:MAX_EVALUATED]


def _stop(candidate: dict, end_station: dict) -> dict | None:
    """The stop where the passenger changes, or None when there is none near
    the far point (or it is the stop of their own end)."""
    far = _far(candidate)
    stop = transit.nearest_named_station(far["lat"], far["lon"])
    if not stop or not stop.get("name") or (stop.get("distance_m") or 0.0) / 1000.0 > MAX_STOP_KM:
        return None
    if stop["name"] == end_station["name"]:
        return None
    return stop


def _leg(candidate: dict, stop: dict, end_station: dict, connections: list[dict]) -> dict | None:
    """The public transport part of a candidate: {from_station, to_station,
    stop, connection, fare, starts, arrives}, with the one of `connections`
    (between `stop` and `end_station`, the stop of the passenger's own end)
    that fits the car's time; None when none does."""
    shape, part = candidate["shape"], candidate["part"]
    change, max_wait = timedelta(minutes=TRANSFER_MIN), timedelta(minutes=MAX_WAIT_MIN)
    if shape == RIDE_FIRST:
        ready = _car_time(candidate) + change
        fitting = [c for c in connections if ready <= _naive(c["departure"]) <= ready + max_wait]
        if not fitting:
            return None
        best = min(fitting, key=lambda c: _naive(c["arrival"]))
        stations = (stop, end_station)
        starts, arrives = part["pickup"]["time"], _naive(best["arrival"])
    else:
        by = _car_time(candidate) - change
        fitting = [c for c in connections if by - max_wait <= _naive(c["arrival"]) <= by]
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


def _own_leg(candidate: dict, stop: dict, end_station: dict) -> dict | None:
    """_leg() with a lookup at the car's own time: the connections that
    leave `stop` once the passenger is out of the car, or get there before
    it leaves."""
    change = timedelta(minutes=TRANSFER_MIN)
    if candidate["shape"] == RIDE_FIRST:
        connections = transit.get_connections(
            stop["name"], end_station["name"], _iso(_car_time(candidate) + change))
    else:
        connections = transit.get_connections(
            end_station["name"], stop["name"], _iso(_car_time(candidate) - change), arrive_by=True)
    return _leg(candidate, stop, end_station, connections)


def _leg_of(candidate: dict, end_station: dict) -> list[tuple[dict, dict]]:
    """A candidate with its public transport part, looked up at the car's
    own time; nothing without a stop or a connection that fits."""
    stop = _stop(candidate, end_station)
    leg = _own_leg(candidate, stop, end_station) if stop else None
    return [(candidate, leg)] if leg else []


def _asked_connections(group: list[dict], end_station: dict, asked: datetime) -> tuple | None:
    """
    For the candidates that change at one place (`group`, the most
    promising first), when public transport is at the end of the trip the
    time is about: (the stop there, the candidates near it, the connections
    between it and `end_station` that arrive by the time asked for, or leave
    from it); None without a stop or a connection.
    """
    stop = _stop(group[0], end_station)
    if stop is None:
        return None
    group = [group[0]] + [
        c for c in group[1:]
        if matching.haversine_km(_far(c)["lat"], _far(c)["lon"], stop["lat"], stop["lon"]) <= MAX_STOP_KM
    ]
    if group[0]["shape"] == RIDE_FIRST:
        connections = transit.get_connections(stop["name"], end_station["name"], _iso(asked), arrive_by=True)
        connections = [c for c in connections if _naive(c["arrival"]) < asked + timedelta(minutes=1)]
    else:
        earliest = asked - timedelta(minutes=EARLY_START_MIN)
        connections = transit.get_connections(end_station["name"], stop["name"], _iso(earliest))
        connections = [c for c in connections if _naive(c["departure"]) >= earliest]
    return (stop, group, connections) if connections else None


def _legs_at(group: list[dict], end_station: dict, asked: datetime) -> list[tuple[dict, dict]]:
    """
    The candidates that change at one place (`group`, the most promising
    first) with their public transport part, when public transport is at
    the end of the trip the time is about: after the car for a time to
    arrive by, before it for a time to leave at.

    There, which drive makes a trip in time is the timetable's to say (the
    last bus that arrives in time may leave hours before the time asked
    for), so one lookup at the time asked for serves them all: the
    connections that arrive by it, or leave from it, and the drives that
    fit one of them. When none does but a drive is at the stop well before
    those connections (or well after), the closest of them gets a lookup at
    its own time. At most MAX_PER_TRANSFER, the latest start first (to
    leave at a time: the earliest arrival).
    """
    at = _asked_connections(group, end_station, asked)
    if at is None:
        return []
    stop, group, connections = at
    shape = group[0]["shape"]
    change, max_wait = timedelta(minutes=TRANSFER_MIN), timedelta(minutes=MAX_WAIT_MIN)
    found = [(c, leg) for c in group if (leg := _leg(c, stop, end_station, connections)) is not None]
    if not found:
        if shape == RIDE_FIRST:
            first = min(_naive(c["departure"]) for c in connections)
            one = max((c for c in group if _car_time(c) + change + max_wait < first), key=_car_time, default=None)
        else:
            last = max(_naive(c["arrival"]) for c in connections)
            one = min((c for c in group if _car_time(c) - change - max_wait > last), key=_car_time, default=None)
        leg = _own_leg(one, stop, end_station) if one else None
        found = [(one, leg)] if leg else []

    # The best first, a real drive before a demo one at the same time; two
    # drives at the same time make one proposal (as in _find).
    found.sort(key=lambda f: bool(f[0]["drive"].get("is_demo")))
    if shape == RIDE_FIRST:
        found.sort(key=lambda f: f[1]["starts"], reverse=True)
    else:
        found.sort(key=lambda f: f[1]["arrives"])
    unique = {}
    for c, leg in found:
        key = (_iso(c["part"]["pickup"]["time"]), _iso(c["part"]["dropoff"]["time"]), leg["connection"]["departure"])
        unique.setdefault(key, (c, leg))
    return list(unique.values())[:MAX_PER_TRANSFER]


def _between_at(group: list[dict], stations: dict, asked: datetime) -> list[tuple[dict, dict, dict]]:
    """
    The rides between two public transport legs that change at one place at
    the end of the trip the time is about, each as (candidate, the leg
    before the car, the leg after it). `group`: the candidates seen from
    that end, the most promising first: as a ride first when the time is
    one to arrive by (the leg after the car is the one in question), as a
    ride last otherwise. `stations`: the passenger's own stop at each end,
    by the shape that ends there.

    The leg at that end as in _legs_at, with one lookup at the time asked
    for; the other leg at the car's own time, for the MAX_PER_TRANSFER most
    promising of the drives that fit (two at the same times count as one).
    """
    asked_end = group[0]["shape"]
    other_end = TRANSIT_FIRST if asked_end == RIDE_FIRST else RIDE_FIRST
    at = _asked_connections(group, stations[asked_end], asked)
    if at is None:
        return []
    stop, group, connections = at
    found, tried = [], set()
    for c in group:
        leg = _leg(c, stop, stations[asked_end], connections)
        times = (_iso(c["part"]["pickup"]["time"]), _iso(c["part"]["dropoff"]["time"]), _change_place({**c, "shape": other_end}))
        if leg is None or times in tried:
            continue
        tried.add(times)
        for _, other in _leg_of({**c, "shape": other_end}, stations[other_end]):
            found.append((c, other, leg) if asked_end == RIDE_FIRST else (c, leg, other))
        if len(tried) >= MAX_PER_TRANSFER:
            break
    return found


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


def _as_between(candidate: dict, before: dict, after: dict, off_min: float) -> dict:
    """A ride between two public transport legs as a match: _as_match() with
    "transit_before" and "transit_after" for "transit", and "change_before"
    and "change_after" (the stops where the passenger gets in and out) for
    "change"."""
    trip = _as_match({**candidate, "shape": RIDE_FIRST}, after, off_min)
    del trip["transit"], trip["change"]
    trip["pickup"]["name"] = before["stop"]["name"]
    return {
        **trip,
        "shape": TRANSIT_BOTH,
        "transit_before": {k: before[k] for k in ("from_station", "to_station", "connection", "fare")},
        "transit_after": {k: after[k] for k in ("from_station", "to_station", "connection", "fare")},
        "change_before": {k: before["stop"][k] for k in ("name", "lat", "lon")},
        "change_after": {k: after["stop"][k] for k in ("name", "lat", "lon")},
        "starts": _iso(before["starts"]),
        "arrives": _iso(after["arrives"]),
        "total_min": round(_total_min(before["starts"], after["arrives"])),
    }


def _same_trip(trip: dict) -> tuple:
    """What two proposals of the same trip have in common: its shape, the
    times of the car and the connections."""
    legs = ("transit_before", "transit_after") if trip["shape"] == TRANSIT_BOTH else ("transit",)
    return (trip["shape"], trip["pickup"]["time"], trip["dropoff"]["time"],
            *((trip[leg]["from_station"], trip[leg]["to_station"], trip[leg]["connection"]["departure"]) for leg in legs))


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
    leave out what is much slower; or a function that gives it, called once
    the trips are found (the caller can look it up meanwhile). Never raises: without the timetable
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
    stations = {}
    alone = []

    def alone_min() -> float | None:
        """Public transport alone, in minutes: asked for when the first trip
        is compared with it."""
        if not alone:
            alone.append(_transit_alone_min(transit_option() if callable(transit_option) else transit_option))
        return alone[0]

    def station(shape: str) -> dict | None:
        """The passenger's own stop at the end where a trip of `shape` is by
        public transport: their destination for a ride first, their start
        for a ride last. Looked up when first needed."""
        if shape not in stations:
            point = destination if shape == RIDE_FIRST else origin
            stations[shape] = transit.resolve_station(point.lat, point.lon, point.name if point.kind == "place" else None)
        return stations[shape]

    def results(futures: list, budget_s: float) -> list:
        """What the lookups found within `budget_s`; the others are dropped."""
        done, late = wait(futures, timeout=budget_s)
        for future in late:
            future.cancel()
        found = []
        for future in done:
            try:
                found += future.result()
            except Exception:
                logger.warning("combined trip lookup failed", exc_info=True)
        return found

    def proposed(starts: datetime, arrives: datetime, as_trip, slower: float, grace_min: float) -> dict | None:
        """The trip `as_trip(minutes off the time asked for)` when it is in
        time and not longer than `slower` times public transport alone
        plus the grace."""
        off = _off_by_min(starts, arrives, asked, time_kind, time_tolerance_min)
        if off is None:
            return None
        total_min = _total_min(starts, arrives)
        if alone_min() is not None and total_min > alone_min() * slower + grace_min:
            return None
        trip = as_trip(off)
        if alone_min() is not None:
            trip["vs_transit_min"] = round(total_min - alone_min())  # negative: quicker than public transport alone
        return trip

    def best(trips: list[dict]) -> list[dict]:
        # Two drives at the same time on the same trip make one proposal: the real one, if any.
        unique = {}
        for trip in sorted(trips, key=lambda t: bool(t.get("is_demo"))):
            unique.setdefault(_same_trip(trip), trip)
        if time_kind == "arrive":
            return sorted(unique.values(), key=lambda t: (t["starts"], t["arrives"]), reverse=True)
        return sorted(unique.values(), key=lambda t: (t["arrives"], t["starts"]))

    # Public transport is at the end of the trip the time is about for one
    # shape: a lookup per place to change at (_legs_at). For the other, the
    # car is, and each drive's own time decides: a lookup per drive.
    asked_end = RIDE_FIRST if time_kind == "arrive" else TRANSIT_FIRST
    began = time.monotonic()
    # Next to enough drives for the whole way, only what can be booked.
    enough = len(exclude_ids) >= MAX_RESULTS
    with search._search_slots:  # routes are parsed: one search's worth at a time
        candidates = _candidates(start, end, asked, time_kind, time_tolerance_min, radius_km, exclude_ids,
                                 include_demo=not enough)
    futures = []
    for shape in (RIDE_FIRST, TRANSIT_FIRST):
        if not any(c["shape"] == shape for c in candidates) or not station(shape):
            continue
        if shape == asked_end:
            futures += [_pool.submit(_legs_at, group, station(shape), asked) for group in _by_place(candidates, shape)]
        else:
            futures += [_pool.submit(_leg_of, c, station(shape)) for c in _pick(candidates, shape)]
    trips = []
    for candidate, leg in results(futures, BUDGET_S) if futures else []:
        trip = proposed(leg["starts"], leg["arrives"], lambda off: _as_match(candidate, leg, off),
                        MAX_SLOWER_THAN_TRANSIT, SLOWER_GRACE_MIN)
        if trip:
            trips.append(trip)
    trips = best(trips)

    # With room left in what the search shows, the drives that are near
    # neither end: a ride between two public transport legs.
    left_s = BUDGET_S - (time.monotonic() - began)
    if len(trips) + len(exclude_ids) < MAX_RESULTS and left_s >= 1.0:
        shown = exclude_ids | {t["id"] for t in trips}
        with search._search_slots:
            between = _between_candidates(start, end, asked, time_kind, time_tolerance_min, radius_km, shown)
        found = []
        if between and station(RIDE_FIRST) and station(TRANSIT_FIRST):
            seen = [{**c, "shape": asked_end} for c in between]
            found = results([
                _pool.submit(_between_at, group, stations, asked)
                for group in _by_place(seen, asked_end)[:MAX_BETWEEN_PLACES]
            ], left_s)
        # Their routes, for the map: only those of the trips found.
        with search._search_slots:
            geometries = db.route_geometries(list({c["drive"]["id"] for c, _, _ in found}))
            for candidate, _, _ in found:
                candidate["route"] = matching.prepare_route(geometries.get(candidate["drive"]["id"]), candidate["drive"])
        for candidate, before, after in found:
            trip = proposed(before["starts"], after["arrives"], lambda off: _as_between(candidate, before, after, off),
                            MAX_SLOWER_BETWEEN, SLOWER_BETWEEN_GRACE_MIN)
            if trip:
                trips.append(trip)
        trips = best(trips)
    return trips[:MAX_RESULTS]
