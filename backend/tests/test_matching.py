"""
Regression tests for matching.find_matches() -- the driver/passenger
detour-ranking algorithm -- against real, previously-reported cases.

Each entry in CASES is a driver's posted drive, a passenger's ride request,
and what the algorithm is expected to do with that pair. Append new cases
here as new bug reports / scenarios come in; nothing else needs to change
to pick them up (test_find_matches is parametrized over CASES).
"""
from dataclasses import dataclass

import matching
import pytest


@dataclass
class Case:
    name: str
    note: str  # why this case exists / what it guards against
    drive: dict
    passenger_origin: tuple
    passenger_destination: tuple
    requested_time: str
    expect_match: bool
    expect_driver: str | None = None
    max_detour_km: float | None = None


def _drive(driver_name, origin, destination, departure_time, seats_available=3):
    (origin_lat, origin_lon) = origin
    (destination_lat, destination_lon) = destination
    return {
        "driver_name": driver_name,
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
        "destination_lat": destination_lat,
        "destination_lon": destination_lon,
        "destination_name": f"{driver_name}'s destination",
        "departure_time": departure_time,
        "seats_available": seats_available,
    }


CASES = [
    Case(
        name="bernard_aigle_bern__marie_villeneuve_bern",
        note=(
            "Real bug: Marie's destination had been geocoded to an obscure street "
            "'Berne 210, 9064 Hundwil' (~150km away, see 'address auto complete.md') "
            "instead of the city of Bern, so Bernard's Aigle->Bern drive never matched. "
            "With the destination corrected to Bern, the two routes overlap almost "
            "entirely and this must match."
        ),
        drive=_drive(
            "Bernard",
            origin=(46.317161560058594, 6.966929912567139),  # Rue du Rhône 9, 1860 Aigle
            destination=(46.94083023071289, 7.4473981857299805),  # Hallwylstrasse 4, 3005 Bern
            departure_time="2026-09-22T18:00",
        ),
        passenger_origin=(46.3940315246582, 6.923530101776123),  # Villeneuve VD
        passenger_destination=(46.954559326171875, 7.420684814453125),  # Bern (BE)
        requested_time="2026-09-22T18:00",
        expect_match=True,
        expect_driver="Bernard",
        max_detour_km=8.0,
    ),
    Case(
        name="matteo_locarno_chur__marie_riazzino_versam__same_day",
        note=(
            "Real bug report: Marie (Riazzino->Versam) didn't see Matteo's "
            "Locarno->Chur drive. Riazzino sits right next to Locarno and Versam "
            "sits near Chur, so with a correctly-aligned request time this must "
            "match (detour ~7-31km depending on OSRM vs haversine, comfortably "
            "under the 40% cap either way)."
        ),
        drive=_drive(
            "Matteo",
            origin=(46.166812896728516, 8.80155086517334),  # Via Simone da Locarno, Locarno
            destination=(46.8482666015625, 9.533320426940918),  # Museumstrasse 3, Chur
            departure_time="2026-09-23T15:00",
        ),
        passenger_origin=(46.18094253540039, 8.88644790649414),  # Riazzino
        passenger_destination=(46.80705642700195, 9.311906814575195),  # Versam
        requested_time="2026-09-23T15:15",
        expect_match=True,
        expect_driver="Matteo",
        max_detour_km=10.0,
    ),
    Case(
        name="matteo_locarno_chur__marie_riazzino_versam__date_typo",
        note=(
            "Same drive/request as above, but the passenger's requested_time is a "
            "full day later (24h15m gap) -- a data-entry typo in the original "
            "report, not a case the matcher should accommodate. Product decision "
            "(2026-09-21): the 3h MAX_TIME_WINDOW_HOURS stays as-is; a next-day "
            "request must NOT match a today/tomorrow drive just because the "
            "time-of-day happens to line up. This case pins that decision down so "
            "a future change to the time filter doesn't silently widen it."
        ),
        drive=_drive(
            "Matteo",
            origin=(46.166812896728516, 8.80155086517334),
            destination=(46.8482666015625, 9.533320426940918),
            departure_time="2026-09-23T15:00",
        ),
        passenger_origin=(46.18094253540039, 8.88644790649414),
        passenger_destination=(46.80705642700195, 9.311906814575195),
        requested_time="2026-09-24T15:15",
        expect_match=False,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_find_matches(case, force_haversine):
    matches = matching.find_matches(
        [case.drive], case.passenger_origin, case.passenger_destination, case.requested_time
    )

    if not case.expect_match:
        assert matches == [], f"{case.name}: expected no match ({case.note})"
        return

    assert matches, f"{case.name}: expected a match but got none ({case.note})"
    assert matches[0]["driver_name"] == case.expect_driver, case.note
    if case.max_detour_km is not None:
        assert matches[0]["detour_km"] <= case.max_detour_km, (
            f"{case.name}: detour {matches[0]['detour_km']}km exceeds expected bound "
            f"({case.note})"
        )


def test_seats_and_time_window_still_filter(force_haversine):
    """Sanity check that the coarse filters (seats, time window) still work
    independently of the detour math -- guards against a future change to
    find_matches breaking the basic filtering while all the CASES above
    (which don't exercise these edges) keep passing."""
    drive = CASES[0].drive

    no_seats = {**drive, "seats_available": 0}
    assert matching.find_matches(
        [no_seats], CASES[0].passenger_origin, CASES[0].passenger_destination, CASES[0].requested_time
    ) == []

    far_future = {**drive, "departure_time": "2026-09-30T18:00"}
    assert matching.find_matches(
        [far_future], CASES[0].passenger_origin, CASES[0].passenger_destination, CASES[0].requested_time
    ) == []


def test_only_the_most_promising_drives_are_routed(force_haversine, monkeypatch):
    """With many drives in the time window, only MAX_ROUTED_CANDIDATES are
    routed (one routing call each), picked by straight-line detour, so the
    best fit is still found."""
    routed = []
    real_compute_detour = matching.compute_detour

    def counting_compute_detour(drive, *args):
        routed.append(drive["driver_name"])
        return real_compute_detour(drive, *args)

    monkeypatch.setattr(matching, "compute_detour", counting_compute_detour)

    lausanne, geneve = (46.5197, 6.6323), (46.2044, 6.1432)
    # Same direction, but a bigger straight-line detour than the drive along
    # the lake (11.7 km vs 4.7 km).
    vevey = (46.4628, 6.8419)
    off_route = [_drive(f"From Vevey {i}", vevey, geneve, "2026-09-22T08:00") for i in range(20)]
    best = _drive("Along the lake", lausanne, geneve, "2026-09-22T08:00")
    morges, nyon = (46.5089, 6.4979), (46.3833, 6.2333)

    matches = matching.find_matches([*off_route, best], morges, nyon, "2026-09-22T08:00")

    assert len(routed) == matching.MAX_ROUTED_CANDIDATES
    assert "Along the lake" in routed
    assert matches[0]["driver_name"] == "Along the lake"


def test_passenger_going_the_other_way_is_not_matched(force_haversine):
    """Real bug: Samedan -> St. Moritz matched a St. Moritz -> Scuol drive
    (12 km of detour, under the 40% threshold), though the driver would have
    to turn back."""
    st_moritz, scuol = (46.4908, 9.8355), (46.7966, 10.2980)
    samedan, zernez = (46.5340, 9.8730), (46.6990, 10.0920)
    drive = _drive("Men Cadonau", st_moritz, scuol, "2026-09-22T17:20")

    assert matching.find_matches([drive], samedan, st_moritz, "2026-09-22T17:30") == []
    # Also when the driver's destination lies beyond both, as seen from the
    # passenger: Poschiavo is south of Samedan and St. Moritz alike.
    poschiavo = (46.3240, 10.0580)
    to_poschiavo = _drive("Mirco Lardi", st_moritz, poschiavo, "2026-09-22T17:30")
    assert matching.find_matches([to_poschiavo], samedan, st_moritz, "2026-09-22T17:30") == []
    # The same drive still matches a passenger heading its way.
    assert len(matching.find_matches([drive], samedan, zernez, "2026-09-22T17:30")) == 1
