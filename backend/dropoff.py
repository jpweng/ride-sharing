"""
Best-drop-off search: combines matching.py (route/detour math) and
transit.py (real Swiss stops/connections) to answer a question the plain
detour-to-exact-destination model in matching.py can't: instead of forcing
the driver to divert all the way to the passenger's exact destination and
back, could the driver drop them somewhere closer to the destination, but
still *on* their own route -- with a real bus/train covering the rest?

Concretely: for a matched drive, sample points along the driver's route,
snap each to the nearest real transit stop, and for each candidate compute
  detour_km = distance(driver origin -> passenger pickup -> candidate)
              - distance(driver origin -> candidate, along the drive's own route)
i.e. only the cost of the pickup diversion -- reaching the candidate itself
isn't a detour, it's already on the way to the driver's own destination.
The candidate with the smallest detour that still has a working transit
connection onward to the passenger's actual destination wins.

Falls back to None (letting callers keep the original "detour all the way
to the exact destination" behavior) when there's no route geometry to
sample, or no candidate both saves distance and has a working connection.
"""
import matching
import transit

SAMPLE_STEP_KM = 10.0
MAX_STATION_WALK_M = 750.0  # a "nearest station" further than this isn't a realistic drop-off
WALKABLE_GAP_KM = 0.3  # close enough to the passenger's destination that no last-mile leg is needed
MAX_CONNECTION_CHECKS = 4  # cap get_connections() calls per match -- transport.opendata.ch is rate-limited


def _route_candidates(drive: dict) -> list[dict]:
    """
    Real transit stops along the drive's route, each tagged with its
    cumulative driving distance from the drive's origin. Deduplicated by
    station name. sample_route_points always includes the route's own
    endpoint as the last sample, so the driver's destination is naturally
    covered by this loop -- via whatever real station is right there --
    without needing a separate fallback entry (which would carry the raw
    posted address string as a "station name" and fail to resolve against
    the connections API).
    """
    samples = matching.sample_route_points(drive.get("route_geometry"), SAMPLE_STEP_KM)
    if not samples:
        samples = [(
            drive["destination_lat"], drive["destination_lon"],
            (drive.get("route_distance_m") or 0) / 1000.0,
        )]

    seen_names = set()
    candidates = []
    for lat, lon, cumulative_km in samples:
        station = transit.nearest_named_station(lat, lon)
        if not station or not station["name"] or station["name"] in seen_names:
            continue
        if station["distance_m"] is not None and station["distance_m"] > MAX_STATION_WALK_M:
            continue
        seen_names.add(station["name"])
        candidates.append({
            "station_name": station["name"],
            "lat": station["lat"],
            "lon": station["lon"],
            "route_km": cumulative_km,
        })
    return candidates


def find_best_dropoff(
    drive: dict,
    passenger_origin: tuple[float, float],
    passenger_destination: tuple[float, float],
    requested_time: str | None = None,
) -> dict | None:
    """
    Returns the best drop-off candidate for this drive/passenger pair:
        {station_name, lat, lon, detour_km, dropoff_gap_km, last_mile_transit}
    or None if no candidate could be evaluated (no route geometry, or every
    candidate either adds more distance than it saves or strands the
    passenger with no onward connection).
    """
    candidates = _route_candidates(drive)
    if not candidates:
        return None

    # Phase 1: score every candidate on distance alone (an OSRM call each,
    # but no transit lookups yet) -- driver detour plus how much of the trip
    # is still left for the passenger from there. Ranking by detour_km alone
    # would pick a stop that's convenient for the driver but useless for the
    # passenger (e.g. a station 5 minutes from the pickup with a 2-hour
    # onward bus ride); the whole point here is to shorten the passenger's
    # *total* trip, not just the driver's.
    ranked = []
    for candidate in candidates:
        via = matching.route_with_fallback([
            (drive["origin_lat"], drive["origin_lon"]),
            passenger_origin,
            (candidate["lat"], candidate["lon"]),
        ])
        detour_km = max(0.0, via["distance_m"] / 1000.0 - candidate["route_km"])
        dropoff_gap_km = matching.haversine_km(
            candidate["lat"], candidate["lon"],
            passenger_destination[0], passenger_destination[1],
        )
        ranked.append({**candidate, "detour_km": detour_km, "dropoff_gap_km": dropoff_gap_km})
    ranked.sort(key=lambda c: (c["detour_km"] + c["dropoff_gap_km"], c["detour_km"]))

    # Phase 2: only now confirm an actual transit connection exists, and
    # only for the top few candidates in that ranking -- transport.opendata.ch
    # is a free, rate-limited demo API, and a connections lookup per
    # candidate (there can be a dozen+ stations along a long route) would
    # burn through that budget for one match request. Walk the ranking in
    # order and take the first candidate that's actually usable.
    dest_station_name = None
    dest_station_looked_up = False

    for candidate in ranked[:MAX_CONNECTION_CHECKS]:
        if candidate["dropoff_gap_km"] <= WALKABLE_GAP_KM:
            return {
                "station_name": candidate["station_name"],
                "lat": candidate["lat"],
                "lon": candidate["lon"],
                "detour_km": round(candidate["detour_km"], 2),
                "dropoff_gap_km": round(candidate["dropoff_gap_km"], 2),
                "last_mile_transit": None,
            }

        if not dest_station_looked_up:
            dest_station = transit.nearest_named_station(*passenger_destination)
            dest_station_name = dest_station["name"] if dest_station else None
            dest_station_looked_up = True
        if not dest_station_name:
            continue

        connections = transit.get_connections(candidate["station_name"], dest_station_name, requested_time)
        if not connections:
            continue

        return {
            "station_name": candidate["station_name"],
            "lat": candidate["lat"],
            "lon": candidate["lon"],
            "detour_km": round(candidate["detour_km"], 2),
            "dropoff_gap_km": round(candidate["dropoff_gap_km"], 2),
            "last_mile_transit": {
                "from_station": candidate["station_name"],
                "to_station": dest_station_name,
                "connections": connections,
            },
        }

    return None
