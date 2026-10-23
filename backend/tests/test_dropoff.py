"""
Tests for dropoff.find_best_dropoff() -- picking the best point *along* a
driver's own route to let a passenger off, rather than always forcing the
driver all the way to the passenger's exact destination and back.

Uses real coordinates from the Locarno->Chur / Riazzino->Versam case (see
test_matching.py and the "couldn't the driver drop her off closer, e.g.
Tamins/Bonaduz/Domat?" conversation that prompted this module) and real
station names/coordinates recorded from transport.opendata.ch, but through
the `fake_transit` fixture -- so these run fully offline and deterministically
against a free, rate-limited public API.
"""
import dropoff
import matching
import pytest

LOCARNO = (46.166812896728516, 8.80155086517334)
CHUR = (46.8482666015625, 9.533320426940918)
RIAZZINO = (46.18094253540039, 8.88644790649414)
VERSAM = (46.80705642700195, 9.311906814575195)

# A simplified version of the real OSRM Locarno->Chur route geometry
# (points recorded live, ~10km apart along the actual road, in [lon, lat]
# order as matching.py / the DB store it).
LOCARNO_CHUR_ROUTE_GEOMETRY = [
    [8.801329, 46.166769],
    [8.894512, 46.153276],
    [8.997122, 46.178156],  # near Sementina
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
    [9.429216, 46.831867],  # near Domat/Ems
    [9.533320, 46.848267],  # Chur (route endpoint)
]


def _drive(route_geometry, destination_name="Chur"):
    return {
        "origin_lat": LOCARNO[0],
        "origin_lon": LOCARNO[1],
        "destination_lat": CHUR[0],
        "destination_lon": CHUR[1],
        "destination_name": destination_name,
        "route_geometry": route_geometry,
        # Deliberately not the real OSRM distance (139.6km): under
        # force_haversine, "via" distances are computed as straight lines,
        # so the "base" distance needs to be on the same (haversine) basis
        # for detour_km = via - base to be meaningful. Leaving this None
        # makes matching.compute_detour() derive it the same way.
        "route_distance_m": None,
    }


CONNECTION = [{"departure": "2026-09-23T15:57:00", "arrival": "2026-09-23T16:15:00", "duration": "00:18:00",
               "transfers": 1, "products": ["B", "RE"]}]


def test_prefers_closer_destination_over_smaller_driver_detour(force_haversine, fake_transit, dense_dropoff_sampling):
    """
    Regression test for a real bug hit while building this feature: an early
    version ranked candidates by driver detour alone, and picked a station
    right next to the pickup (near-zero detour) over one much further down
    the route -- even though the near-pickup station left the passenger a
    74km/3-hour bus ride from Versam, while the further one (Domat/Ems, what
    a person would actually suggest -- see Tamins/Bonaduz/Domat) left an
    18-minute hop. The fix: rank by detour + remaining last-mile distance,
    not detour alone.
    """
    fake_transit.add_station("Sementina, Ponte sul Ticino", 46.177935, 8.99584)  # the "trap": tiny detour, 74km from Versam
    fake_transit.add_station("Domat/Ems, Parc industrial", 46.830489, 9.428246)  # correct pick: on the way, ~9km from Versam
    fake_transit.add_station("Chur, Bahnhof", *CHUR)
    fake_transit.add_station("Versam-Safien", *VERSAM)
    fake_transit.set_connections("Sementina, Ponte sul Ticino", "Versam-Safien", CONNECTION)
    fake_transit.set_connections("Domat/Ems, Parc industrial", "Versam-Safien", CONNECTION)
    fake_transit.set_connections("Chur, Bahnhof", "Versam-Safien", CONNECTION)

    result = dropoff.find_best_dropoff(_drive(LOCARNO_CHUR_ROUTE_GEOMETRY), RIAZZINO, VERSAM, "2026-09-23T15:15")

    assert result is not None
    assert result["station_name"] == "Domat/Ems, Parc industrial"
    # Sanity bounds from the live-recorded numbers: Domat/Ems is ~9km from
    # Versam and a small detour off the direct Locarno-Chur road; Sementina
    # would have been ~74km from Versam.
    assert result["dropoff_gap_km"] < 15
    assert result["detour_km"] < 15


def test_falls_back_to_none_when_no_candidate_has_a_connection(force_haversine, fake_transit, dense_dropoff_sampling):
    """If every candidate along the route is either far from the passenger's
    destination or has no onward transit connection, the caller (main.py)
    should fall back to its own old behavior -- signaled by None here."""
    fake_transit.add_station("Sementina, Ponte sul Ticino", 46.177935, 8.99584)
    fake_transit.add_station("Domat/Ems, Parc industrial", 46.830489, 9.428246)
    # No connections registered anywhere, and neither station is walkable to Versam.

    result = dropoff.find_best_dropoff(_drive(LOCARNO_CHUR_ROUTE_GEOMETRY), RIAZZINO, VERSAM, "2026-09-23T15:15")

    assert result is None


def test_walkable_dropoff_needs_no_transit_lookup(force_haversine, fake_transit, dense_dropoff_sampling):
    """A candidate within WALKABLE_GAP_KM of the passenger's destination
    should win without ever calling get_connections for it."""
    # A route that actually reaches Versam (unlike LOCARNO_CHUR_ROUTE_GEOMETRY,
    # whose closest approach to Versam is ~9km at Domat/Ems) -- straight line
    # is fine here, this test is about the walkable-shortcut behavior, not
    # route realism.
    route_via_versam = [
        [LOCARNO[1], LOCARNO[0]],
        [VERSAM[1], VERSAM[0]],
        [CHUR[1], CHUR[0]],
    ]
    close_lat, close_lon = VERSAM[0] + 0.001, VERSAM[1]  # ~100m away, well under WALKABLE_GAP_KM
    fake_transit.add_station("Versam, essentially there", close_lat, close_lon)

    result = dropoff.find_best_dropoff(_drive(route_via_versam), RIAZZINO, VERSAM, "2026-09-23T15:15")

    assert result is not None
    assert result["station_name"] == "Versam, essentially there"
    assert result["last_mile_transit"] is None
    assert fake_transit.connection_calls == []


def test_caps_number_of_connection_lookups(force_haversine, fake_transit):
    """
    transport.opendata.ch is a free, rate-limited demo API (see transit.py) --
    a real request hit "429 Too many requests" while this feature was being
    built, from a single match doing a get_connections() call per candidate
    station along a long route. Guard the cap: no more than
    dropoff.MAX_CONNECTION_CHECKS connections lookups per find_best_dropoff
    call, no matter how many candidate stations there are.
    """
    # One station near every sampled route point (~10km apart on a ~140km
    # route -> well more candidates than the cap), none of them close enough
    # to Versam to short-circuit via the walkable-distance path, and none
    # with a registered connection -- so every candidate is a real lookup
    # attempt, up to the cap.
    for i, (lon, lat) in enumerate(LOCARNO_CHUR_ROUTE_GEOMETRY):
        fake_transit.add_station(f"Stop {i}", lat, lon)

    result = dropoff.find_best_dropoff(_drive(LOCARNO_CHUR_ROUTE_GEOMETRY), RIAZZINO, VERSAM, "2026-09-23T15:15")

    assert result is None  # nothing had a registered connection
    assert len(fake_transit.connection_calls) <= dropoff.MAX_CONNECTION_CHECKS


def test_no_route_geometry_falls_back_gracefully(force_haversine, fake_transit):
    """A haversine-fallback drive (or one posted before route_geometry was
    stored) has no polyline to sample -- should still consider the driver's
    own destination as a candidate rather than crashing or silently finding
    nothing to evaluate."""
    fake_transit.add_station("Chur, Bahnhof", *CHUR)
    fake_transit.set_connections("Chur, Bahnhof", "Versam-Safien", CONNECTION)
    fake_transit.add_station("Versam-Safien", *VERSAM)

    drive = _drive(route_geometry=None)
    result = dropoff.find_best_dropoff(drive, RIAZZINO, VERSAM, "2026-09-23T15:15")

    assert result is not None
    assert result["station_name"] == "Chur, Bahnhof"


@pytest.mark.parametrize("passenger_origin,passenger_destination", [(RIAZZINO, VERSAM)])
def test_matches_the_live_recorded_outcome(
    force_haversine, fake_transit, dense_dropoff_sampling, passenger_origin, passenger_destination
):
    """
    End-to-end sanity check pinned to the actual numbers observed against
    the live OSRM + transport.opendata.ch APIs on 2026-09-21 for this exact
    scenario, so a future refactor that changes the *ranking outcome* (not
    just internal structure) gets caught even though this test itself runs
    offline. Recorded live result: Domat/Ems, Parc industrial, detour
    5.65km/4.0%, gap 9.23km, an 18-minute/1-transfer connection -- vs. the
    naive "detour all the way to Chur and back" baseline of 31.17km/22.3%.
    """
    fake_transit.add_station("Domat/Ems, Parc industrial", 46.830489, 9.428246)
    fake_transit.add_station("Versam-Safien", *VERSAM)
    fake_transit.set_connections("Domat/Ems, Parc industrial", "Versam-Safien", CONNECTION)

    drive = _drive(LOCARNO_CHUR_ROUTE_GEOMETRY)
    baseline = matching.compute_detour(drive, passenger_origin, passenger_destination)
    result = dropoff.find_best_dropoff(drive, passenger_origin, passenger_destination, "2026-09-23T15:15")

    assert result is not None
    assert result["detour_km"] < baseline["detour_km"]
    assert result["dropoff_gap_km"] < 15


# The Engadin case that exposed pointless drop-offs: a St. Moritz -> Scuol
# drive, simplified to its towns, in [lon, lat] order.
ST_MORITZ = (46.4908, 9.8355)
SAMEDAN = (46.5340, 9.8730)
GUARDA = (46.7760, 10.1520)
ENGADIN_ROUTE_GEOMETRY = [
    [9.8355, 46.4908],   # St. Moritz
    [9.8650, 46.4960],   # Celerina
    [9.8730, 46.5340],   # Samedan
    [9.9300, 46.5833],   # La Punt
    [10.0920, 46.6990],  # Zernez
    [10.1110, 46.7690],  # Lavin
    [10.2980, 46.7966],  # Scuol
]


def _engadin_drive():
    return {
        "origin_lat": ST_MORITZ[0], "origin_lon": ST_MORITZ[1],
        "destination_lat": 46.7966, "destination_lon": 10.2980,
        "destination_name": "Scuol",
        "route_geometry": ENGADIN_ROUTE_GEOMETRY,
        "route_distance_m": None,
    }


def _engadin_stations(fake_transit):
    for name, (lon, lat) in zip(
        ["St. Moritz", "Celerina", "Samedan", "La Punt", "Zernez", "Lavin", "Scuol-Tarasp"],
        ENGADIN_ROUTE_GEOMETRY,
    ):
        fake_transit.add_station(name, lat, lon)


def test_no_dropoff_that_takes_the_passenger_nowhere(force_haversine, fake_transit, dense_dropoff_sampling):
    """
    Real bug: a passenger from Samedan to St. Moritz (the way the driver came
    from) got Samedan itself as the "drop-off": zero detour for the driver,
    but the car took them nowhere and the train did the whole trip. No stop
    after the pickup gets them closer to St. Moritz, so there's no drop-off.
    """
    _engadin_stations(fake_transit)
    for station in ["Samedan", "La Punt", "Zernez", "Lavin", "Scuol-Tarasp"]:
        fake_transit.set_connections(station, "St. Moritz", CONNECTION)

    assert dropoff.find_best_dropoff(_engadin_drive(), SAMEDAN, ST_MORITZ, "2026-09-23T15:15") is None


def test_dropoff_comes_after_the_pickup(force_haversine, fake_transit, dense_dropoff_sampling, monkeypatch):
    """Guarda is off the route, a few km from Lavin (its nearest station), and
    only the pickup station has a connection there: the pickup must not be
    offered as the drop-off, so there's no drop-off rather than a pointless one."""
    # Check every candidate, so the pickup station would be reached.
    monkeypatch.setattr(dropoff, "MAX_CONNECTION_CHECKS", 10)
    _engadin_stations(fake_transit)
    fake_transit.set_connections("Samedan", "Lavin", CONNECTION)

    assert dropoff.find_best_dropoff(_engadin_drive(), SAMEDAN, GUARDA, "2026-09-23T15:15") is None


def test_dropoff_further_along_still_found(force_haversine, fake_transit, dense_dropoff_sampling):
    _engadin_stations(fake_transit)
    fake_transit.set_connections("Samedan", "Lavin", CONNECTION)
    fake_transit.set_connections("Zernez", "Lavin", CONNECTION)

    result = dropoff.find_best_dropoff(_engadin_drive(), SAMEDAN, (46.80, 10.12), "2026-09-23T15:15")

    assert result is not None
    assert result["station_name"] in {"Zernez", "Lavin"}
