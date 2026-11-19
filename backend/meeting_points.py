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
Lookups are limited so a search can't burst on the service: at most
MAX_NAME_LOOKUPS different uncached points, a time budget NAME_BUDGET_S, and
a stop after MAX_MISSES unanswered lookups in a row (the service being down).
Failures are remembered briefly. No name is not an error: the page falls
back to the wording without it.
"""
import logging
import math
import time
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
NAME_BUDGET_S = 3.0
MAX_MISSES = 3
# A stop lookup that found nothing (or failed) isn't repeated for this long.
MISS_TTL_S = 120
_misses = transit._TtlCache(2000)


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 4), round(lon, 4))


def _walk_min(km: float) -> int:
    return max(1, round(km * matching.MINUTES_PER_GAP_KM))


class _Namer:
    """Names points for one search, within its limits."""

    def __init__(self):
        self.lookups = 0
        self.misses_in_a_row = 0
        self.started = time.monotonic()
        self.names: dict = {}

    def name(self, lat: float, lon: float) -> str | None:
        key = _key(lat, lon)
        if key in self.names:
            return self.names[key]
        if _misses.get(("miss",) + key):
            return None
        if (
            self.lookups >= MAX_NAME_LOOKUPS
            or self.misses_in_a_row >= MAX_MISSES
            or time.monotonic() - self.started > NAME_BUDGET_S
        ):
            return None
        self.lookups += 1
        name = None
        try:
            stop = transit.nearest_named_station(lat, lon)
            if stop and stop.get("name") and (stop.get("distance_m") or 0.0) <= NAME_MAX_STOP_M:
                name = stop["name"]
        except Exception:  # a name is never worth failing a search
            logger.warning("meeting point name lookup failed", exc_info=True)
        if name:
            self.misses_in_a_row = 0
        else:
            self.misses_in_a_row += 1
            _misses.put(("miss",) + key, True, MISS_TTL_S)
        self.names[key] = name
        return name


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
            pickup["walk_min"] = _walk_min(pickup["gap_km"] * WALK_DETOUR_FACTOR)

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

    namer = _Namer()
    for m in matches:
        for point in (m["pickup"], m["dropoff"]):
            try:
                point["name"] = namer.name(point["lat"], point["lon"])
            except Exception:
                point["name"] = None
