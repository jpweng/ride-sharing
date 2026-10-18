"""
Swiss public transport integration via transport.opendata.ch.

transport.opendata.ch is a free, unofficial, no-API-key-required API built
on the same data SBB and the other Swiss operators publish (it covers
trains, trams, buses, boats -- the whole country, not just SBB). That
makes it a good fit for a prototype: real Swiss transit data, zero
registration friction.

Two things are exposed here:
  - nearest_station(lat, lon): resolve a map point to the closest stop,
    since the connections endpoint works on station names, not coordinates.
  - get_connections(from_station, to_station, when): journey options
    between two stations.

Both fail soft (return None / []) on any network or parsing error, so the
rest of the app can treat "no transit data available" as a normal case --
useful both for genuine outages and for this build sandbox, which does not
have outbound access to this host.
"""
import requests
from datetime import datetime

TRANSPORT_API_BASE = "https://transport.opendata.ch/v1"
REQUEST_TIMEOUT_S = 6


def nearest_station(lat: float, lon: float) -> dict | None:
    """
    Resolve a coordinate to the nearest public transport stop.
    Note: this API's `x`/`y` query params are (lat, lon), not (lon, lat).
    """
    try:
        resp = requests.get(
            f"{TRANSPORT_API_BASE}/locations",
            params={"x": lat, "y": lon, "type": "coordinate"},
            timeout=REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        stations = data.get("stations", [])
        if not stations:
            return None
        nearest = stations[0]
        return {
            "name": nearest.get("name"),
            "distance_m": nearest.get("distance"),
            "coordinate": nearest.get("coordinate"),
        }
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def nearest_named_station(lat: float, lon: float) -> dict | None:
    """
    Like nearest_station, but for callers that need real coordinates to route
    to/from the stop (see dropoff.py), not just a name to hand to
    get_connections. The /locations endpoint's first hit is often a bare
    "this exact coordinate as an address" pseudo-result (id and coordinate
    both null) rather than an actual stop -- fine as a connections()
    endpoint, useless as a point to route to. This skips those and returns
    the closest entry that has real coordinates.
    """
    try:
        resp = requests.get(
            f"{TRANSPORT_API_BASE}/locations",
            params={"x": lat, "y": lon, "type": "coordinate"},
            timeout=REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        for station in data.get("stations", []):
            coordinate = station.get("coordinate") or {}
            if coordinate.get("x") is None or coordinate.get("y") is None:
                continue
            return {
                "name": station.get("name"),
                "lat": coordinate["x"],
                "lon": coordinate["y"],
                "distance_m": station.get("distance"),
            }
        return None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def get_connections(from_station: str, to_station: str, when_iso: str | None = None, limit: int = 3) -> list[dict]:
    """
    Fetch up to `limit` journey options between two named stations.
    when_iso: ISO 8601 datetime string for the desired departure time;
    defaults to "now" (the API's own default) if omitted/unparseable.
    Returns [] if the API is unreachable or returns no connections.
    """
    params = {"from": from_station, "to": to_station, "limit": limit}
    if when_iso:
        try:
            dt = datetime.fromisoformat(when_iso)
            params["date"] = dt.strftime("%Y-%m-%d")
            params["time"] = dt.strftime("%H:%M")
        except ValueError:
            pass

    try:
        resp = requests.get(f"{TRANSPORT_API_BASE}/connections", params=params, timeout=REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        connections = data.get("connections", [])
    except (requests.RequestException, ValueError, KeyError):
        return []

    simplified = []
    for conn in connections[:limit]:
        try:
            from_dep = conn["from"]["departure"]
            to_arr = conn["to"]["arrival"]
            products = sorted({
                sec.get("journey", {}).get("category")
                for sec in conn.get("sections", [])
                if sec.get("journey") and sec["journey"].get("category")
            })
            simplified.append({
                "departure": from_dep,
                "arrival": to_arr,
                "duration": conn.get("duration"),
                "transfers": conn.get("transfers"),
                "products": products,
            })
        except (KeyError, TypeError):
            continue
    return simplified


def transit_option_for_trip(
    origin_lat: float, origin_lon: float,
    destination_lat: float, destination_lon: float,
    when_iso: str | None = None,
) -> dict | None:
    """
    Convenience wrapper: resolve both ends to stations and fetch
    connections. Returns None if station lookup or connection search fails
    (e.g. no network access to transport.opendata.ch).
    """
    origin_station = nearest_station(origin_lat, origin_lon)
    dest_station = nearest_station(destination_lat, destination_lon)
    if not origin_station or not dest_station:
        return None

    connections = get_connections(origin_station["name"], dest_station["name"], when_iso)
    if not connections:
        return None

    return {
        "from_station": origin_station["name"],
        "to_station": dest_station["name"],
        "connections": connections,
    }
