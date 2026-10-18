"""
Integration tests through the actual /api/match request handler
(main.match_ride), the same code path the frontend hits -- not just the
underlying matching.py / dropoff.py functions in isolation. This is the
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


def test_bernard_marie_bern_match(isolated_db, force_haversine, fake_transit):
    fake_transit.add_station("Bern, Forsthaus", 46.95197, 7.417567)
    fake_transit.set_connections("Hallwylstrasse 4 3005 Bern", "Bern, Forsthaus", CONNECTION)

    _post_drive(
        "Bernard",
        origin=("Rue du Rhône 9 1860 Aigle", 46.317161560058594, 6.966929912567139),
        destination=("Hallwylstrasse 4 3005 Bern", 46.94083023071289, 7.4473981857299805),
        departure_time=f"{DAY_1}T18:00",
    )

    payload = main.MatchRequest(
        passenger_name="Marie",
        origin={"name": "Villeneuve VD", "lat": 46.3940315246582, "lon": 6.923530101776123},
        destination={"name": "Bern (BE)", "lat": 46.954559326171875, "lon": 7.420684814453125},
        requested_time=f"{DAY_1}T18:00",
    )
    result = main.match_ride(payload)

    assert [m["driver_name"] for m in result["driver_matches"]] == ["Bernard"]
    assert result["driver_matches"][0]["detour_km"] < 8.0


def test_matteo_marie_versam_match_with_recommended_dropoff(
    isolated_db, force_haversine, fake_transit, dense_dropoff_sampling
):
    fake_transit.add_station("Domat/Ems, Parc industrial", 46.830489, 9.428246)
    fake_transit.add_station("Versam-Safien", 46.80705642700195, 9.311906814575195)
    fake_transit.set_connections("Domat/Ems, Parc industrial", "Versam-Safien", CONNECTION)

    drive = _post_drive(
        "Matteo",
        origin=("Via Simone da Locarno # 6600 Locarno", 46.166812896728516, 8.80155086517334),
        destination=("Museumstrasse 3 7000 Chur", 46.8482666015625, 9.533320426940918),
        departure_time=f"{DAY_2}T15:00",
    )
    # post_drive computes route_geometry itself via matching.route_with_fallback,
    # which is forced to haversine (a straight line) by the force_haversine
    # fixture -- overwrite it with a realistic curved polyline so the
    # route-sampling in dropoff.py has something meaningful to snap stations
    # to, matching what a real OSRM-backed drive would have stored.
    import database as db
    with db.get_conn() as conn:
        import json
        conn.execute(
            "UPDATE drives SET route_geometry = ? WHERE id = ?",
            (json.dumps(LOCARNO_CHUR_ROUTE_GEOMETRY), drive["id"]),
        )

    payload = main.MatchRequest(
        passenger_name="Marie",
        origin={"name": "Riazzino", "lat": 46.18094253540039, "lon": 8.88644790649414},
        destination={"name": "Versam", "lat": 46.80705642700195, "lon": 9.311906814575195},
        requested_time=f"{DAY_2}T15:15",
    )
    result = main.match_ride(payload)

    matches = result["driver_matches"]
    assert [m["driver_name"] for m in matches] == ["Matteo"]
    match = matches[0]
    assert match["recommended_dropoff"] is not None
    assert match["recommended_dropoff"]["station_name"] == "Domat/Ems, Parc industrial"
    assert match["last_mile_transit"]["to_station"] == "Versam-Safien"
    # The refined, route-aware detour must be materially smaller than the
    # naive "detour all the way to Chur and back" baseline of ~31km/22%
    # that the original bug report was filed against.
    assert match["detour_km"] < 15.0


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
