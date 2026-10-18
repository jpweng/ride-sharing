"""
Swiss public transport integration via transport.opendata.ch.

transport.opendata.ch is a free, unofficial, no-API-key-required API built
on the same data SBB and the other Swiss operators publish (it covers
trains, trams, buses, boats -- the whole country, not just SBB). That
makes it a good fit for a prototype: real Swiss transit data, zero
registration friction.

Two things are exposed here:
  - resolve_station(lat, lon, place_name): resolve a passenger's endpoint
    to a stop -- the town's train station if they picked a town by name,
    else the nearest stop -- since the connections endpoint works on
    station names, not coordinates.
  - get_connections(from_station, to_station, when): journey options
    between two stations, with the operator of each leg and an SBB
    booking link.

Both fail soft (return None / []) on any network or parsing error, so the
rest of the app can treat "no transit data available" as a normal case --
useful both for genuine outages and for this build sandbox, which does not
have outbound access to this host.
"""
import re

import requests
from datetime import datetime
from urllib.parse import urlencode

import matching
import operators

TRANSPORT_API_BASE = "https://transport.opendata.ch/v1"
REQUEST_TIMEOUT_S = 6

# How far a town's train station may be from the point geo.admin gives for
# that town (the municipality's centroid) and still count as "its" station.
# Guards against same-named places elsewhere in the country.
PLACE_STATION_MAX_KM = 10.0

# SBB's timetable takes stops as "<name>_I<station id>" joined by "~", the
# date as YYYY-MM-DD and the time as HH_MM (verified against the live site:
# it opens with the journey prefilled, priced and bookable). One SBB ticket
# covers every Swiss operator on the journey, so this is the booking link
# even for connections that don't involve SBB itself.
SBB_TIMETABLE_URL = "https://www.sbb.ch/en"


def _nearby_stops(lat: float, lon: float) -> list[dict]:
    """
    Real public transport stops near a coordinate, closest first.

    The /locations endpoint's first hit is usually a bare "this coordinate as
    a street address" pseudo-result (e.g. "En Moillesaulaz 1, 1844 Villeneuve
    (VD)") rather than a stop. It has no station id -- and, depending on the
    API's mood, may or may not have a coordinate -- and /connections can't
    route from it (it just returns no connections). Only entries with an id
    are actual stops, so everything else is dropped here.
    Note: this API's `x`/`y` query params are (lat, lon), not (lon, lat).
    Raises on network/parse errors; callers fail soft.
    """
    resp = requests.get(
        f"{TRANSPORT_API_BASE}/locations",
        params={"x": lat, "y": lon, "type": "coordinate"},
        timeout=REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    stops = []
    for station in resp.json().get("stations", []):
        coordinate = station.get("coordinate") or {}
        if not station.get("id") or coordinate.get("x") is None or coordinate.get("y") is None:
            continue
        stops.append(station)
    return stops


def _named_station(station: dict, distance_m: float | None) -> dict:
    return {
        "id": station.get("id"),
        "name": station.get("name"),
        "lat": station["coordinate"]["x"],
        "lon": station["coordinate"]["y"],
        "distance_m": distance_m,
    }


def nearest_named_station(lat: float, lon: float) -> dict | None:
    """
    Resolve a coordinate to the nearest public transport stop, as
    {id, name, lat, lon, distance_m}, or None.
    """
    try:
        stops = _nearby_stops(lat, lon)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
    if not stops:
        return None
    return _named_station(stops[0], stops[0].get("distance"))


def _search_stations(query: str) -> list[dict]:
    resp = requests.get(
        f"{TRANSPORT_API_BASE}/locations",
        params={"query": query, "type": "station"},
        timeout=REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json().get("stations", [])


def station_for_place(place_name: str, lat: float, lon: float) -> dict | None:
    """
    The station of a town/municipality the user picked by name (e.g.
    "Villeneuve (VD)"), rather than whichever stop happens to be nearest the
    municipality's centroid -- someone who types a town means "from there",
    and in Switzerland that's the train station.

    Searches stations by name and takes the first *train* station in the
    API's relevance order, which puts the main station first ("Davos Platz"
    before "Davos Dorf", "Zürich HB" before the rest), restricted to
    stations within PLACE_STATION_MAX_KM of the given point so a same-named
    town elsewhere can't win. The search runs on the bare name first:
    geo.admin's canton suffix ("Bern (BE)") throws the API's ranking off
    (it ranks tram stops and "Wabern bei Bern" above Bern station). Towns
    with no train station (Saas-Fee) get their first bus/tram/boat stop by
    name instead -- cable cars are skipped, they're usually up a mountain.
    None if nothing matches (or the API is unreachable), so callers can fall
    back to the nearest stop.
    """
    bare_name = re.sub(r"\s*\([A-Z]{2}\)$", "", place_name).strip()
    queries = [bare_name] if bare_name == place_name else [bare_name, place_name]

    fallback = None
    for query in queries:
        try:
            stations = _search_stations(query)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return fallback
        for station in stations:
            coordinate = station.get("coordinate") or {}
            if not station.get("id") or coordinate.get("x") is None or coordinate.get("y") is None:
                continue
            distance_km = matching.haversine_km(lat, lon, coordinate["x"], coordinate["y"])
            if distance_km > PLACE_STATION_MAX_KM:
                continue
            if station.get("icon") == "train":
                return _named_station(station, round(distance_km * 1000.0))
            if fallback is None and station.get("icon") != "cableway":
                fallback = _named_station(station, round(distance_km * 1000.0))
    return fallback


def resolve_station(lat: float, lon: float, place_name: str | None = None) -> dict | None:
    """
    The stop a passenger's journey should start/end at: the town's train
    station when they picked a town by name (place_name given), otherwise
    -- for a street address or a point on the map -- the nearest stop.
    """
    if place_name:
        station = station_for_place(place_name, lat, lon)
        if station:
            return station
    return nearest_named_station(lat, lon)


def sbb_booking_url(from_name: str, from_id: str, to_name: str, to_id: str, departure_iso: str | None) -> str | None:
    """Link to this journey in SBB's timetable/ticket shop (see SBB_TIMETABLE_URL)."""
    if not (from_name and from_id and to_name and to_id):
        return None
    params = {"stops": f"{from_name}_I{from_id}~{to_name}_I{to_id}"}
    # departure_iso is the API's local Swiss time, e.g. "2026-10-18T18:57:00+0200".
    if departure_iso and len(departure_iso) >= 16:
        params["day"] = departure_iso[:10]
        params["time"] = departure_iso[11:16].replace(":", "_")
    params["moment"] = "dep"
    return f"{SBB_TIMETABLE_URL}?{urlencode(params, safe='/~,')}"


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
            journeys = [sec["journey"] for sec in conn.get("sections", []) if sec.get("journey")]
            products = sorted({j["category"] for j in journeys if j.get("category")})
            legs = [
                {
                    "category": j.get("category"),
                    "number": j.get("number"),
                    "operator": operators.operator_info(j.get("operator")),
                }
                for j in journeys
            ]
            # Each company once, in the order the passenger meets them.
            journey_operators = []
            for leg in legs:
                op = leg["operator"]
                if op and op["code"] not in {o["code"] for o in journey_operators}:
                    journey_operators.append(op)
            from_station = conn["from"].get("station") or {}
            to_station = conn["to"].get("station") or {}
            simplified.append({
                "departure": from_dep,
                "arrival": to_arr,
                "duration": conn.get("duration"),
                "transfers": conn.get("transfers"),
                "products": products,
                "legs": legs,
                "operators": journey_operators,
                "booking_url": sbb_booking_url(
                    from_station.get("name"), from_station.get("id"),
                    to_station.get("name"), to_station.get("id"),
                    from_dep,
                ),
            })
        except (KeyError, TypeError):
            continue
    return simplified


def transit_option_for_trip(
    origin_lat: float, origin_lon: float,
    destination_lat: float, destination_lon: float,
    when_iso: str | None = None,
    origin_place: str | None = None,
    destination_place: str | None = None,
) -> dict | None:
    """
    Convenience wrapper: resolve both ends to stations and fetch
    connections. origin_place / destination_place are the town names when
    the user picked a town rather than an address (see resolve_station).
    Returns None if station lookup or connection search fails (e.g. no
    network access to transport.opendata.ch).
    """
    origin_station = resolve_station(origin_lat, origin_lon, origin_place)
    dest_station = resolve_station(destination_lat, destination_lon, destination_place)
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
