"""
What a search result says about its pickup and drop-off points, so a match
can be understood without the map (routes/match.py calls add_to() after
transit_legs.add_to()). For each match:

  pickup:  "name" (a place name, or None) and "walk_min" (to get there)
  dropoff: "name" and "onward": the rest of the way to the passenger's
           destination, {walk_min, car_min, transit_min, walk_km,
           car_km, source}, or None when the point is practically at
           the destination (NEAR_KM); it is structured data, the page words it.

Where the numbers come from:
  - car: one OSRM `table` call for all the matches' drop-offs (matching.py,
    same timeouts and circuit breaker as routes). Without OSRM it is an
    estimate from the straight-line gap (CAR_DETOUR_FACTOR, matching's
    fallback speed); then "source" is "estimate".
  - on foot: an ESTIMATE, not a walking route: a distance at walking pace
    (matching.MINUTES_PER_GAP_KM, the pace that ranks matches). The distance
    is OSRM's road distance when we have it (never below the straight line),
    otherwise the straight line times WALK_DETOUR_FACTOR.
  - public transport: derived from the last-mile leg transit_legs.py already
    looked up: from the car's drop-off time to the arrival of the first
    connection. No leg, no "transit_min". It makes no timetable call.

Place names: the nearest public transport stop (transit.nearest_named_station,
the same free service and cache as the timetable, so no new party receives
coordinates), when it is at most NAME_MAX_STOP_M away. Not OSRM's `nearest`:
its street names depend on data our server is run without (--disable-feature-
dataset ROUTE_STEPS), and a street name without its locality says little.
Lookups are limited so a search neither waits long nor bursts on the service:
at most MAX_NAME_LOOKUPS different uncached points per search, those of the
rides that can be booked first, asked NAME_WORKERS at a time (for the whole
app, not per search) and given NAME_BUDGET_S in all. When the service
doesn't answer within that, nobody asks it again for PAUSE_S. A point that
got no name isn't asked again for MISS_TTL_S. No name is not an error: the
page falls back to the wording without it.
"""
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

import matching
import transit

logger = logging.getLogger("uvicorn.error")

# A pickup or drop-off this close to the passenger's own start or
# destination gets no onward times: they are there.
NEAR_KM = 0.2

# Straight line -> distance by foot / by car when no road distance is known.
WALK_DETOUR_FACTOR = 1.3
CAR_DETOUR_FACTOR = 1.3

NAME_MAX_STOP_M = 500
MAX_NAME_LOOKUPS = 12
NAME_WORKERS = 4
NAME_BUDGET_S = 3.0
# After a search whose lookups didn't all answer within NAME_BUDGET_S.
PAUSE_S = 60.0
# A stop lookup that found nothing (or failed) isn't repeated for this long.
MISS_TTL_S = 120
_misses = transit._TtlCache(2000)
# One pool for the app: at most NAME_WORKERS lookups at the service at once,
# however many searches are running.
_pool = ThreadPoolExecutor(max_workers=NAME_WORKERS, thread_name_prefix="meeting-point-names")
_paused_until = 0.0


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 4), round(lon, 4))


def _walk_min(km: float) -> int:
    return max(1, round(km * matching.MINUTES_PER_GAP_KM))


def walk_min(gap_km: float) -> int:
    """The walk across a straight-line gap, without a road distance."""
    return _walk_min(gap_km * WALK_DETOUR_FACTOR)


def _lookup(lat: float, lon: float) -> str | None:
    """The stop nearest the point, if it is near enough to name it."""
    stop = transit.nearest_named_station(lat, lon)
    if stop and stop.get("name") and (stop.get("distance_m") or 0.0) <= NAME_MAX_STOP_M:
        return stop["name"]
    return None


def _names(points: list[dict]) -> dict:
    """{_key(point): name or None} for `points` ({lat, lon}, the most wanted
    first), within the limits above. A point left out has no name."""
    global _paused_until
    names: dict = {}
    wanted = []
    for point in points:
        key = _key(point["lat"], point["lon"])
        if key in names or key in wanted:
            continue
        if _misses.get(("miss",) + key):
            names[key] = None
        elif len(wanted) < MAX_NAME_LOOKUPS:
            wanted.append(key)
    if not wanted or time.monotonic() < _paused_until:
        return names

    futures = {_pool.submit(_lookup, *key): key for key in wanted}
    done, late = wait(futures, timeout=NAME_BUDGET_S)
    for future in late:
        future.cancel()  # those not started yet; the others end on their own timeout
    if late:
        _paused_until = time.monotonic() + PAUSE_S
        logger.warning("meeting point names: %d of %d lookups unanswered after %.0f s, pausing for %.0f s",
                       len(late), len(futures), NAME_BUDGET_S, PAUSE_S)
    for future in done:
        key = futures[future]
        try:
            name = future.result()
        except Exception:  # a name is never worth failing a search
            logger.warning("meeting point name lookup failed", exc_info=True)
            name = None
        names[key] = name
        if not name:
            _misses.put(("miss",) + key, True, MISS_TTL_S)
    return names


def _car(dropoffs: list[dict], destination: tuple[float, float]) -> list[dict]:
    """[{km, min, source}] per drop-off: OSRM's, else an estimate."""
    table = None
    if dropoffs:
        try:
            table = matching.osrm_table([(d["lat"], d["lon"]) for d in dropoffs], destination)
        except Exception:
            logger.warning("OSRM table lookup failed", exc_info=True)
    out = []
    for i, d in enumerate(dropoffs):
        straight = matching.haversine_km(d["lat"], d["lon"], *destination)
        if table and table[i]:
            out.append({"km": table[i][0] / 1000.0, "min": table[i][1] / 60.0, "source": "osrm", "straight_km": straight})
        else:
            km = straight * CAR_DETOUR_FACTOR
            out.append({"km": km, "min": km / matching.FALLBACK_SPEED_KMH * 60.0, "source": "estimate", "straight_km": straight})
    return out


def _transit_min(match: dict) -> int | None:
    """Minutes from the car's drop-off to the arrival of the first connection
    of the last-mile leg (waiting included), or None without a leg."""
    leg = match.get("last_mile_transit")
    if not leg:
        return None
    try:
        dropped = datetime.fromisoformat(match["dropoff"]["time"]).replace(tzinfo=None)
        arrivals = [datetime.fromisoformat(c["arrival"]).replace(tzinfo=None) for c in leg["connections"]]
    except (KeyError, TypeError, ValueError):
        return None
    minutes = [(a - dropped).total_seconds() / 60.0 for a in arrivals]
    minutes = [m for m in minutes if m > 0]
    return math.ceil(min(minutes)) if minutes else None


def add_to(matches: list[dict], origin, destination) -> None:
    """Adds the names, walking time and onward times described above to each
    match. `origin` and `destination` are the search's own (api.LatLon)."""
    dest = (destination.lat, destination.lon)
    for m in matches:
        pickup, dropoff = m["pickup"], m["dropoff"]
        pickup["name"] = None
        pickup["walk_min"] = None
        dropoff["name"] = None
        dropoff["onward"] = None
        if pickup["gap_km"] > NEAR_KM:
            pickup["walk_min"] = walk_min(pickup["gap_km"])

    far = [m for m in matches if m["dropoff"]["gap_km"] > NEAR_KM]
    try:
        cars = _car([m["dropoff"] for m in far], dest)
        for m, car in zip(far, cars):
            walk_km = car["km"] if car["source"] == "osrm" else car["straight_km"] * WALK_DETOUR_FACTOR
            walk_km = max(walk_km, car["straight_km"])
            m["dropoff"]["onward"] = {
                "walk_min": _walk_min(walk_km),
                "car_min": max(1, round(car["min"])),
                "transit_min": _transit_min(m),
                "walk_km": round(walk_km, 1),
                "car_km": round(car["km"], 1),
                "source": car["source"],
            }
    except Exception:
        logger.warning("onward times failed", exc_info=True)
        for m in far:
            m["dropoff"]["onward"] = None

    # Names: the rides that can be booked first, then in the list's order.
    try:
        first = sorted(matches, key=lambda m: not m.get("bookable"))
        names = _names([point for m in first for point in (m["pickup"], m["dropoff"])])
        for m in matches:
            for point in (m["pickup"], m["dropoff"]):
                point["name"] = names.get(_key(point["lat"], point["lon"]))
    except Exception:
        logger.warning("meeting point names failed", exc_info=True)
