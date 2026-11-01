"""
Integration tests through the actual /api/match request handler
(main.match_ride), the same code path the frontend hits -- not just the
underlying matching.py functions in isolation. This is the
layer where the real reported bugs actually surfaced (a drive posted, a
request posted, no match shown), so it's worth covering end-to-end even
though the pieces are unit-tested separately.

Runs against an isolated temp sqlite DB (isolated_db fixture) and fully
mocked OSRM/transit lookups (force_haversine, fake_transit) -- never the
real rideshare.db, never the live network.
"""
from datetime import date, timedelta

import main

# match_ride only considers upcoming drives (departure_time >= now), so all
# dates are relative to today rather than hardcoded -- a fixed date would
# silently stop matching once it slipped into the past.
DAY_1 = (date.today() + timedelta(days=1)).isoformat()
DAY_2 = (date.today() + timedelta(days=2)).isoformat()
DAY_3 = (date.today() + timedelta(days=3)).isoformat()

CONNECTION = [{"departure": f"{DAY_1}T18:00:00", "arrival": f"{DAY_1}T18:20:00", "duration": "00:20:00",
               "transfers": 1, "products": ["B"]}]

AIGLE_BERN_ROUTE_GEOMETRY = [
    [6.966930, 46.317162],  # Aigle
    [6.923530, 46.394032],  # Villeneuve
    [6.841900, 46.462800],  # Vevey
    [6.900000, 46.527000],  # Châtel-Saint-Denis
    [7.056900, 46.619300],  # Bulle
    [7.161900, 46.806500],  # Fribourg
    [7.447398, 46.940830],  # Bern
]

LOCARNO_CHUR_ROUTE_GEOMETRY = [
    [8.801329, 46.166769],
    [8.894512, 46.153276],
    [8.997122, 46.178156],
    [9.088171, 46.231135],
    [9.176187, 46.277369],
    [9.222139, 46.358965],
    [9.230719, 46.415653],
    [9.187473, 46.462355],
    [9.222109, 46.535192],
    [9.340597, 46.558382],
    [9.427715, 46.590735],
    [9.448996, 46.672218],
    [9.426895, 46.754648],
    [9.429216, 46.831867],
    [9.533320, 46.848267],
]


def _post_drive(driver_name, origin, destination, departure_time, seats_available=3):
    payload = main.DriveCreate(
        driver_name=driver_name,
        origin={"name": origin[0], "lat": origin[1], "lon": origin[2]},
        destination={"name": destination[0], "lat": destination[1], "lon": destination[2]},
        departure_time=departure_time,
        seats_available=seats_available,
    )
    return main.post_drive(payload)


def _set_route(drive, geometry):
    # post_drive computes route_geometry itself via matching.route_with_fallback,
    # which is forced to haversine (a straight line) by the force_haversine
    # fixture -- overwrite it with a realistic curved polyline, matching what
    # a real OSRM-backed drive would have stored.
    import json
    import database as db
    with db.get_conn() as conn:
        conn.execute("UPDATE drives SET route_geometry = ? WHERE id = ?", (json.dumps(geometry), drive["id"]))


def test_bernard_marie_bern_match(isolated_db, force_haversine, fake_transit):
    drive = _post_drive(
        "Bernard",
        origin=("Rue du Rhône 9 1860 Aigle", 46.317161560058594, 6.966929912567139),
        destination=("Hallwylstrasse 4 3005 Bern", 46.94083023071289, 7.4473981857299805),
        departure_time=f"{DAY_1}T18:00",
    )
    _set_route(drive, AIGLE_BERN_ROUTE_GEOMETRY)

    payload = main.MatchRequest(
        passenger_name="Marie",
        origin={"name": "Villeneuve VD", "lat": 46.3940315246582, "lon": 6.923530101776123},
        destination={"name": "Bern (BE)", "lat": 46.954559326171875, "lon": 7.420684814453125},
        requested_time=f"{DAY_1}T18:00",
    )
    result = main.match_ride(payload)

    assert [m["driver_name"] for m in result["driver_matches"]] == ["Bernard"]
    match = result["driver_matches"][0]
    assert match["pickup"]["gap_km"] < 1.0
    assert match["dropoff"]["gap_km"] < 5.0


def test_matteo_marie_versam_match_with_last_mile(isolated_db, force_haversine, fake_transit):
    fake_transit.add_station("Domat/Ems, Parc industrial", 46.830489, 9.428246)
    fake_transit.add_station("Versam-Safien", 46.80705642700195, 9.311906814575195)
    fake_transit.set_connections("Domat/Ems, Parc industrial", "Versam-Safien", CONNECTION)

    drive = _post_drive(
        "Matteo",
        origin=("Via Simone da Locarno # 6600 Locarno", 46.166812896728516, 8.80155086517334),
        destination=("Museumstrasse 3 7000 Chur", 46.8482666015625, 9.533320426940918),
        departure_time=f"{DAY_2}T15:00",
    )
    _set_route(drive, LOCARNO_CHUR_ROUTE_GEOMETRY)

    def search(dropoff_radius_km):
        return main.match_ride(main.MatchRequest(
            passenger_name="Marie",
            origin={"name": "Riazzino", "lat": 46.18094253540039, "lon": 8.88644790649414},
            destination={"name": "Versam", "lat": 46.80705642700195, "lon": 9.311906814575195},
            requested_time=f"{DAY_2}T15:15",
            dropoff_radius_km=dropoff_radius_km,
        ))["driver_matches"]

    # His route passes ~9 km from Versam: outside the default 5 km drop-off radius...
    assert search(5) == []
    # ...inside a 10 km one, with a bus for the rest of the way.
    matches = search(10)
    assert [m["driver_name"] for m in matches] == ["Matteo"]
    assert matches[0]["last_mile_transit"]["from_station"] == "Domat/Ems, Parc industrial"
    assert matches[0]["last_mile_transit"]["to_station"] == "Versam-Safien"


def test_date_typo_request_yields_no_match(isolated_db, force_haversine, fake_transit):
    """Pins the product decision that a request a full day off from the
    drive's departure should not match, even when the time-of-day is close
    (see test_matching.py's ...__date_typo case for the rationale)."""
    _post_drive(
        "Matteo",
        origin=("Via Simone da Locarno # 6600 Locarno", 46.166812896728516, 8.80155086517334),
        destination=("Museumstrasse 3 7000 Chur", 46.8482666015625, 9.533320426940918),
        departure_time=f"{DAY_2}T15:00",
    )

    payload = main.MatchRequest(
        passenger_name="Marie",
        origin={"name": "Riazzino", "lat": 46.18094253540039, "lon": 8.88644790649414},
        destination={"name": "Versam", "lat": 46.80705642700195, "lon": 9.311906814575195},
        requested_time=f"{DAY_3}T15:15",
    )
    result = main.match_ride(payload)

    assert result["driver_matches"] == []


def test_town_endpoints_use_their_train_stations(isolated_db, force_haversine, fake_transit):
    """A passenger who picks towns by name ("place") gets the public-transport
    alternative between those towns' train stations; one who picks a street
    address or map point at the same spot gets the nearest stop instead."""
    fake_transit.add_station("Veytaux, Grandchamp", 46.409856, 6.931329)
    fake_transit.add_station("Fribourg, Pont de St-Jean", 46.803672, 7.161495)
    fake_transit.add_place_station("Villeneuve (VD)", "Villeneuve VD", 46.398372, 6.926999)
    fake_transit.add_place_station("Fribourg (FR)", "Fribourg/Freiburg", 46.803151, 7.151052)
    fake_transit.set_connections("Villeneuve VD", "Fribourg/Freiburg", CONNECTION)
    fake_transit.set_connections("Veytaux, Grandchamp", "Fribourg, Pont de St-Jean", CONNECTION)

    def search(kind):
        return main.match_ride(main.MatchRequest(
            passenger_name="Antoine",
            origin={"name": "Villeneuve (VD)", "lat": 46.40636, "lon": 6.95311, "kind": kind},
            destination={"name": "Fribourg (FR)", "lat": 46.80337, "lon": 7.16178, "kind": kind},
            requested_time=f"{DAY_1}T18:53",
        ))["transit_option"]

    by_town = search("place")
    assert (by_town["from_station"], by_town["to_station"]) == ("Villeneuve VD", "Fribourg/Freiburg")

    by_address = search("address")
    assert (by_address["from_station"], by_address["to_station"]) == ("Veytaux, Grandchamp", "Fribourg, Pont de St-Jean")


def test_drives_at_the_edge_of_the_time_tolerance_still_match(isolated_db, force_haversine, fake_transit):
    # Only drives near the requested time are loaded; the bounds must not cut
    # off one whose pickup is exactly time_tolerance_min away, whatever its
    # time format. Pickup at Aigle, the drives' start: pickup time = departure.
    aigle = ("Aigle", 46.3180, 6.9700)
    bern = ("Bern", 46.9480, 7.4474)
    _post_drive("Early", aigle, bern, f"{DAY_1}T16:00:00")
    _post_drive("Late", aigle, bern, f"{DAY_1}T20:00")
    _post_drive("Too late", aigle, bern, f"{DAY_1}T20:05")
    _post_drive("Too early", aigle, bern, f"{DAY_1}T15:55")

    payload = main.MatchRequest(
        passenger_name="Marie",
        origin={"name": "Aigle", "lat": 46.3180, "lon": 6.9700},
        destination={"name": "Bern", "lat": 46.9546, "lon": 7.4207},
        requested_time=f"{DAY_1}T18:00",
    )
    result = main.match_ride(payload)

    assert sorted(m["driver_name"] for m in result["driver_matches"]) == ["Early", "Late"]


def test_a_drive_that_left_earlier_matches_by_its_pickup_time(isolated_db, force_haversine, fake_transit):
    # Aigle -> Bern straight line (~80 km at the fallback 60 km/h): a drive
    # leaving Aigle at 16:30 passes Fribourg-ish ~17:30.
    _post_drive("Bernard", ("Aigle", 46.3180, 6.9700), ("Bern", 46.9480, 7.4474), f"{DAY_1}T16:30")

    def search(tolerance):
        return main.match_ride(main.MatchRequest(
            passenger_name="Marie",
            origin={"name": "Near Fribourg", "lat": 46.75, "lon": 7.30},
            destination={"name": "Bern", "lat": 46.9480, "lon": 7.4474},
            requested_time=f"{DAY_1}T17:45",
            time_tolerance_min=tolerance,
        ))["driver_matches"]

    assert [m["driver_name"] for m in search(30)] == ["Bernard"]
    assert search(5) == []
