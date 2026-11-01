"""
Route corridor matching with passenger-chosen search radii.

A passenger says where they start and where they're going, and how far from
each they're willing to meet a driver: the pickup radius and the drop-off
radius (5 km each by default, drawn as circles on the map). A drive matches
when its road route passes within the pickup radius of their start and,
further along, within the drop-off radius of their destination:

    pickup  = the point of the route closest to the passenger's start
    dropoff = the point of the route after the pickup closest to their destination

The driver stays on their own route; the passenger gets to the pickup and
from the drop-off on foot, by bike or by public transport (main.py looks up
a last-mile connection from the drop-off). Departure times are compared at
the pickup, not at the driver's start: a Lausanne -> Zurich drive leaving at
7:00 reaches Bern around 8:00, which is what a passenger in Bern cares about.
Drives whose pickup is within the passenger's time tolerance are kept.

Matches are ranked by the time the passenger loses: minutes between the
pickup and the time they asked for, plus the distance to the pickup and from
the drop-off at walking pace (MINUTES_PER_GAP_KM).

This is all plain geometry on the drives' stored routes: no routing call per
candidate, so a search can consider every drive in the time window and
return many matches. Routes come from the public OSRM demo routing server
(router.project-osrm.org, no API key required) when a drive is posted. If
OSRM is unreachable, route_with_fallback() falls back to a straight line
(haversine distance) so the app keeps working in a degraded mode.
"""
import math
from datetime import datetime, timedelta

import requests

OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_S = 6

# Search defaults; a passenger can change each one (see main.MatchRequest).
DEFAULT_PICKUP_RADIUS_KM = 5.0
DEFAULT_DROPOFF_RADIUS_KM = 5.0
DEFAULT_TIME_TOLERANCE_MIN = 120

MAX_RESULTS = 20

# The car ride must be at least this long along the route: a shorter one
# isn't worth arranging.
MIN_RIDE_KM = 2.0

# Ranking: one km between the passenger and the pickup or drop-off counts
# like this many minutes away from the time they asked for (walking pace).
MINUTES_PER_GAP_KM = 12.0

# The longest drive expected: a drive that left this long before the
# passenger's time window can still reach their pickup inside it.
MAX_DRIVE_HOURS = 6.0

# Route points closer together than this are merged before matching: real
# OSRM routes have a point every few meters, which only slows matching down.
ROUTE_POINT_SPACING_KM = 0.2

# Drives without a known duration (straight-line fallback) are assumed to
# drive at this average speed.
FALLBACK_SPEED_KMH = 60.0

KM_PER_DEGREE = 111.32


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def osrm_route(points: list[tuple[float, float]]) -> dict | None:
    """
    points: list of (lat, lon) tuples, in travel order, length >= 2.
    Returns {"distance_m": float, "duration_s": float, "geometry": [[lon,lat], ...]}
    or None if the request fails for any reason (network, timeout, bad response).
    """
    if len(points) < 2:
        return None
    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coord_str}"
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson"},
            timeout=OSRM_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route = data["routes"][0]
        return {
            "distance_m": route["distance"],
            "duration_s": route["duration"],
            "geometry": route["geometry"]["coordinates"],  # [lon, lat] pairs
        }
    except (requests.RequestException, ValueError, KeyError):
        return None


def route_with_fallback(points: list[tuple[float, float]]) -> dict:
    """
    Try OSRM; if it fails, fall back to straight-line segments summed up.
    Always returns a dict with distance_m, duration_s, geometry, source.
    """
    result = osrm_route(points)
    if result is not None:
        result["source"] = "osrm"
        return result

    # Haversine fallback: sum leg distances, estimate duration at 60 km/h,
    # and use the straight lines themselves as a (very rough) geometry.
    total_km = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        total_km += haversine_km(lat1, lon1, lat2, lon2)
    return {
        "distance_m": total_km * 1000,
        "duration_s": (total_km / 60.0) * 3600.0,
        "geometry": [[lon, lat] for lat, lon in points],
        "source": "haversine",
    }


def prepare_route(route_geometry: list | None, drive: dict) -> list[tuple[float, float, float]]:
    """
    A drive's route as [(lat, lon, km from the start), ...], with points at
    least ROUTE_POINT_SPACING_KM apart (always keeping the first and last).
    route_geometry: [[lon, lat], ...] as stored on a drive; without one, the
    straight line from the drive's origin to its destination.
    """
    if route_geometry and len(route_geometry) >= 2:
        points = [(lat, lon) for lon, lat in route_geometry]
    else:
        points = [
            (drive["origin_lat"], drive["origin_lon"]),
            (drive["destination_lat"], drive["destination_lon"]),
        ]
    route = [(points[0][0], points[0][1], 0.0)]
    km = 0.0
    for i, (lat, lon) in enumerate(points[1:], start=1):
        step = haversine_km(route[-1][0], route[-1][1], lat, lon)
        if step >= ROUTE_POINT_SPACING_KM or i == len(points) - 1:
            km += step
            route.append((lat, lon, km))
    return route


def nearest_on_route(
    route: list[tuple[float, float, float]],
    point: tuple[float, float],
    from_km: float = 0.0,
) -> dict | None:
    """
    The point of `route` (from prepare_route) closest to `point` (lat, lon),
    looking only at the part at least `from_km` along it. Returns
    {lat, lon, gap_km, route_km} or None if that part is empty.

    Projects onto each segment on a local flat approximation around `point`
    -- accurate for the few km a search radius covers -- so sparse routes
    (a straight-line fallback) work as well as dense ones.
    """
    lat0, lon0 = point
    kx = KM_PER_DEGREE * math.cos(math.radians(lat0))
    ky = KM_PER_DEGREE
    best = None
    for (lat1, lon1, km1), (lat2, lon2, km2) in zip(route, route[1:]):
        if km2 < from_km:
            continue
        ax, ay = (lon1 - lon0) * kx, (lat1 - lat0) * ky
        bx, by = (lon2 - lon0) * kx, (lat2 - lat0) * ky
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq == 0 else min(1.0, max(0.0, -(ax * dx + ay * dy) / length_sq))
        if km2 > km1 and km1 < from_km:
            t = max(t, (from_km - km1) / (km2 - km1))
        qx, qy = ax + t * dx, ay + t * dy
        gap_km = math.hypot(qx, qy)
        if best is None or gap_km < best["gap_km"]:
            best = {
                "lat": lat0 + qy / ky,
                "lon": lon0 + qx / kx,
                "gap_km": gap_km,
                "route_km": km1 + t * (km2 - km1),
            }
    return best


def could_pass_near(drive: dict, point: tuple[float, float], radius_km: float) -> bool:
    """
    Cheap test, on the drive's endpoints and road distance only, that rules
    out drives whose route can't come within `radius_km` of `point`: a route
    of length L through a point Q has |start Q| + |Q end| <= L, so if it
    passes within r of P, |start P| + |P end| <= L + 2r. Lets a search skip
    loading the route of almost every drive in its time window.
    """
    start = (drive["origin_lat"], drive["origin_lon"])
    end = (drive["destination_lat"], drive["destination_lon"])
    length_km = (drive.get("route_distance_m") or 0) / 1000.0 or haversine_km(*start, *end)
    # +1 km: merged route points cut corners a little.
    return haversine_km(*start, *point) + haversine_km(*point, *end) <= length_km + 2 * radius_km + 1.0


def _naive(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def match_drive(
    drive: dict,
    passenger_origin: tuple[float, float],
    passenger_destination: tuple[float, float],
    requested_time: str,
    pickup_radius_km: float = DEFAULT_PICKUP_RADIUS_KM,
    dropoff_radius_km: float = DEFAULT_DROPOFF_RADIUS_KM,
    time_tolerance_min: float = DEFAULT_TIME_TOLERANCE_MIN,
) -> dict | None:
    """
    How `drive` serves this passenger, or None if it doesn't: the drive with
    its pickup and drop-off points, the ride's length, the minutes between
    the pickup and the requested time, and the ranking score.
    """
    if drive.get("seats_available", 0) <= 0:
        return None
    if not (
        could_pass_near(drive, passenger_origin, pickup_radius_km)
        and could_pass_near(drive, passenger_destination, dropoff_radius_km)
    ):
        return None

    route = prepare_route(drive.get("route_geometry"), drive)
    pickup = nearest_on_route(route, passenger_origin)
    if pickup is None or pickup["gap_km"] > pickup_radius_km:
        return None
    dropoff = nearest_on_route(route, passenger_destination, from_km=pickup["route_km"] + MIN_RIDE_KM)
    if dropoff is None or dropoff["gap_km"] > dropoff_radius_km:
        return None
    # Getting to the car and from it must be shorter than the trip itself,
    # or the ride isn't worth it (e.g. a drive passing near both ends of a
    # short trip, but the wrong way round).
    if pickup["gap_km"] + dropoff["gap_km"] >= haversine_km(*passenger_origin, *passenger_destination):
        return None

    total_km = route[-1][2]
    duration_s = drive.get("route_duration_s") or total_km / FALLBACK_SPEED_KMH * 3600.0
    departure = _naive(drive["departure_time"])

    def time_at(route_km: float) -> datetime:
        share = route_km / total_km if total_km > 0 else 0.0
        return departure + timedelta(seconds=duration_s * share)

    pickup_time = time_at(pickup["route_km"])
    time_diff_min = (pickup_time - _naive(requested_time)).total_seconds() / 60.0
    if abs(time_diff_min) > time_tolerance_min:
        return None

    score = abs(time_diff_min) + MINUTES_PER_GAP_KM * (pickup["gap_km"] + dropoff["gap_km"])
    return {
        **drive,
        # Simplified: a fraction of the size of the stored route, still
        # plenty for drawing it on the map.
        "route_geometry": [[lon, lat] for lat, lon, _ in route],
        "pickup": {
            "lat": round(pickup["lat"], 6),
            "lon": round(pickup["lon"], 6),
            "gap_km": round(pickup["gap_km"], 1),
            "time": _iso(pickup_time),
        },
        "dropoff": {
            "lat": round(dropoff["lat"], 6),
            "lon": round(dropoff["lon"], 6),
            "gap_km": round(dropoff["gap_km"], 1),
            "time": _iso(time_at(dropoff["route_km"])),
        },
        "ride_km": round(dropoff["route_km"] - pickup["route_km"], 1),
        "time_diff_min": round(time_diff_min),
        "score": round(score, 1),
    }


def find_matches(
    drives: list[dict],
    passenger_origin: tuple[float, float],
    passenger_destination: tuple[float, float],
    requested_time: str,
    pickup_radius_km: float = DEFAULT_PICKUP_RADIUS_KM,
    dropoff_radius_km: float = DEFAULT_DROPOFF_RADIUS_KM,
    time_tolerance_min: float = DEFAULT_TIME_TOLERANCE_MIN,
    max_results: int = MAX_RESULTS,
) -> list[dict]:
    """
    The drives that serve this passenger (see match_drive), best first.
    Drives need their route_geometry; one without it is taken as a straight
    line.
    """
    matches = []
    for drive in drives:
        m = match_drive(
            drive, passenger_origin, passenger_destination, requested_time,
            pickup_radius_km, dropoff_radius_km, time_tolerance_min,
        )
        if m is not None:
            matches.append(m)
    matches.sort(key=lambda m: (m["score"], m["pickup"]["time"]))
    return matches[:max_results]
