"""
Route / detour-based matching.

"Most suitable driver based on trajectory" is implemented as a detour-cost
match: for every posted drive we know the driver's own route (origin ->
destination). For a candidate passenger trip we compute how much *longer*
the driver's route would become if they detoured via the passenger's pickup
and drop-off points:

    detour = distance(origin -> pickup -> dropoff -> destination)
             - distance(origin -> destination)

Drives are ranked by ascending detour (smallest extra distance first) and
filtered out if the detour exceeds a configurable threshold. This is the
practical version of "does the passenger's trip lie along the driver's
corridor" -- rather than buffering a route geometry, we directly measure
how far off-path the driver would have to go, which also gives a natural,
explainable ranking score.

Routes/distances come from the public OSRM demo routing server
(router.project-osrm.org, no API key required). If OSRM is unreachable
(e.g. no internet access, or the demo server is rate-limiting), every
function below transparently falls back to straight-line (haversine)
distance so the app keeps working in a degraded but functional mode.
"""
import math
import requests

OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_S = 6

# Matching thresholds -- tuned for a small country / prototype, not a
# production heuristic.
MAX_DETOUR_KM = 8.0
MAX_DETOUR_PCT = 40.0
MAX_TIME_WINDOW_HOURS = 3.0


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


def compute_detour(
    drive: dict,
    passenger_origin: tuple[float, float],
    passenger_destination: tuple[float, float],
) -> dict:
    """
    Returns detour metrics for one drive against one passenger trip.
    Uses the drive's precomputed base route distance when available, and
    computes the "via passenger" route fresh.
    """
    base_distance_m = drive.get("route_distance_m")
    if base_distance_m is None:
        base = route_with_fallback([
            (drive["origin_lat"], drive["origin_lon"]),
            (drive["destination_lat"], drive["destination_lon"]),
        ])
        base_distance_m = base["distance_m"]

    via_points = [
        (drive["origin_lat"], drive["origin_lon"]),
        passenger_origin,
        passenger_destination,
        (drive["destination_lat"], drive["destination_lon"]),
    ]
    via = route_with_fallback(via_points)

    detour_m = max(0.0, via["distance_m"] - base_distance_m)
    detour_pct = (detour_m / base_distance_m * 100.0) if base_distance_m > 0 else 0.0

    return {
        "base_distance_km": round(base_distance_m / 1000.0, 2),
        "via_distance_km": round(via["distance_m"] / 1000.0, 2),
        "detour_km": round(detour_m / 1000.0, 2),
        "detour_pct": round(detour_pct, 1),
        "via_duration_min": round(via["duration_s"] / 60.0, 1),
        "route_source": via["source"],
    }


def sample_route_points(route_geometry: list | None, step_km: float = 10.0) -> list[tuple[float, float, float]]:
    """
    Sample points along a route polyline at roughly `step_km` intervals of
    cumulative driving distance, for evaluating candidate drop-off points
    along the *whole* route rather than only at its final destination (see
    dropoff.py). Used to answer "could the driver let the passenger off
    somewhere closer to their actual destination, on the way?".

    route_geometry: [[lon, lat], ...] as returned by route_with_fallback /
    stored on a drive. Returns [(lat, lon, cumulative_km), ...], always
    including the route's start and end point. Empty input -> [].
    """
    if not route_geometry or len(route_geometry) < 2:
        return []

    points = [(lat, lon) for lon, lat in route_geometry]
    cumulative = [0.0]
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + haversine_km(lat1, lon1, lat2, lon2))
    total_km = cumulative[-1]

    # Don't oversample a very long route -- cap how many candidates
    # downstream code has to evaluate (each one costs a routing + a transit
    # lookup call).
    step_km = max(step_km, total_km / 20.0) if total_km > 0 else step_km

    samples = []
    next_target = 0.0
    for (lat, lon), dist_km in zip(points, cumulative):
        if dist_km >= next_target:
            samples.append((lat, lon, dist_km))
            next_target = dist_km + step_km
    if samples[-1][2] != total_km:
        samples.append((points[-1][0], points[-1][1], total_km))
    return samples


def hours_between(iso_a: str, iso_b: str) -> float:
    from datetime import datetime
    try:
        a = datetime.fromisoformat(iso_a)
        b = datetime.fromisoformat(iso_b)
        return abs((a - b).total_seconds()) / 3600.0
    except ValueError:
        return 0.0


def find_matches(
    drives: list[dict],
    passenger_origin: tuple[float, float],
    passenger_destination: tuple[float, float],
    requested_time: str,
    max_results: int = 5,
) -> list[dict]:
    """
    Rank candidate drives by detour cost. Filters out drives with no seats,
    departure time too far from the request, or a detour beyond threshold.
    """
    candidates = []
    for drive in drives:
        if drive.get("seats_available", 0) <= 0:
            continue
        if hours_between(drive["departure_time"], requested_time) > MAX_TIME_WINDOW_HOURS:
            continue

        metrics = compute_detour(drive, passenger_origin, passenger_destination)
        if metrics["detour_km"] > MAX_DETOUR_KM and metrics["detour_pct"] > MAX_DETOUR_PCT:
            continue

        candidates.append({**drive, **metrics})

    candidates.sort(key=lambda d: (d["detour_km"], d["detour_pct"]))
    return candidates[:max_results]
