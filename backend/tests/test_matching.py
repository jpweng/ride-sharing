"""
Tests for matching.find_matches() -- route corridor matching with the
passenger's pickup / drop-off radii and time tolerance -- including real,
previously-reported cases.

Each entry in CASES is a driver's posted drive, a passenger's ride request,
and what the algorithm is expected to do with that pair. Append new cases
here as new bug reports / scenarios come in; nothing else needs to change
to pick them up (test_find_matches is parametrized over CASES).
"""
from dataclasses import dataclass, field

import matching
import pytest

# Road-like polylines ([lon, lat]) for the drives below, so the tests don't
# depend on the OSRM server.
AIGLE_BERN_ROUTE = [
    [6.966930, 46.317162],  # Aigle
    [6.923530, 46.394032],  # Villeneuve
    [6.841900, 46.462800],  # Vevey
    [6.900000, 46.527000],  # Châtel-Saint-Denis
    [7.056900, 46.619300],  # Bulle
    [7.161900, 46.806500],  # Fribourg
    [7.447398, 46.940830],  # Bern
]

LOCARNO_CHUR_ROUTE = [
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


def _drive(driver_name, route, departure_time, seats_available=3, duration_min=None):
    (origin_lon, origin_lat), (destination_lon, destination_lat) = route[0], route[-1]
    return {
        "id": hash(driver_name),
        "driver_name": driver_name,
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
        "destination_lat": destination_lat,
        "destination_lon": destination_lon,
        "destination_name": f"{driver_name}'s destination",
        "departure_time": departure_time,
        "seats_available": seats_available,
        "route_geometry": route,
        "route_duration_s": duration_min * 60 if duration_min else None,
    }


@dataclass
class Case:
    name: str
    note: str  # why this case exists / what it guards against
    drive: dict
    passenger_origin: tuple
    passenger_destination: tuple
    requested_time: str
    expect_match: bool
    search: dict = field(default_factory=dict)  # radii / tolerance, else the defaults


VILLENEUVE = (46.3940315246582, 6.923530101776123)
BERN = (46.954559326171875, 7.420684814453125)
RIAZZINO = (46.18094253540039, 8.88644790649414)
VERSAM = (46.80705642700195, 9.311906814575195)

CASES = [
    Case(
        name="bernard_aigle_bern__marie_villeneuve_bern",
        note=(
            "Real bug: Marie's destination had been geocoded to an obscure street "
            "'Berne 210, 9064 Hundwil' (~150km away, see 'address auto complete.md') "
            "instead of the city of Bern, so Bernard's Aigle->Bern drive never matched. "
            "With the destination corrected to Bern, his route runs through Villeneuve "
            "and ends in Bern: this must match."
        ),
        drive=_drive("Bernard", AIGLE_BERN_ROUTE, "2026-09-22T18:00"),
        passenger_origin=VILLENEUVE,
        passenger_destination=BERN,
        requested_time="2026-09-22T18:00",
        expect_match=True,
    ),
    Case(
        name="matteo_locarno_chur__marie_riazzino_versam__default_radius",
        note=(
            "Real bug report: Marie (Riazzino->Versam) didn't see Matteo's "
            "Locarno->Chur drive. His route passes ~3 km from Riazzino but ~9 km "
            "from Versam (at Reichenau), so with the default 5 km drop-off radius "
            "it is correctly not a match..."
        ),
        drive=_drive("Matteo", LOCARNO_CHUR_ROUTE, "2026-09-23T15:00"),
        passenger_origin=RIAZZINO,
        passenger_destination=VERSAM,
        requested_time="2026-09-23T15:15",
        expect_match=False,
    ),
    Case(
        name="matteo_locarno_chur__marie_riazzino_versam__wider_dropoff",
        note="...and with a 10 km drop-off radius it is.",
        drive=_drive("Matteo", LOCARNO_CHUR_ROUTE, "2026-09-23T15:00"),
        passenger_origin=RIAZZINO,
        passenger_destination=VERSAM,
        requested_time="2026-09-23T15:15",
        expect_match=True,
        search={"dropoff_radius_km": 10},
    ),
    Case(
        name="matteo_locarno_chur__marie_riazzino_versam__date_typo",
        note=(
            "Same drive/request, but the passenger's requested_time is a full day "
            "later (24h15m gap) -- a data-entry typo in the original report, not a "
            "case the matcher should accommodate. A next-day request must NOT match "
            "just because the time of day lines up, even with the widest tolerance."
        ),
        drive=_drive("Matteo", LOCARNO_CHUR_ROUTE, "2026-09-23T15:00"),
        passenger_origin=RIAZZINO,
        passenger_destination=VERSAM,
        requested_time="2026-09-24T15:15",
        expect_match=False,
        search={"dropoff_radius_km": 10, "time_tolerance_min": 360},
    ),
    Case(
        name="bernard_aigle_bern__passenger_going_the_other_way",
        note="Bern -> Villeneuve on an Aigle -> Bern drive: the driver would have to turn back.",
        drive=_drive("Bernard", AIGLE_BERN_ROUTE, "2026-09-22T18:00"),
        passenger_origin=BERN,
        passenger_destination=VILLENEUVE,
        requested_time="2026-09-22T18:00",
        expect_match=False,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_find_matches(case):
    matches = matching.find_matches(
        [case.drive], case.passenger_origin, case.passenger_destination, case.requested_time, **case.search
    )

    if not case.expect_match:
        assert matches == [], f"{case.name}: expected no match ({case.note})"
        return
    assert matches, f"{case.name}: expected a match but got none ({case.note})"
    m = matches[0]
    assert m["driver_name"] == case.drive["driver_name"]
    assert m["pickup"]["gap_km"] <= case.search.get("pickup_radius_km", matching.DEFAULT_PICKUP_RADIUS_KM)
    assert m["dropoff"]["gap_km"] <= case.search.get("dropoff_radius_km", matching.DEFAULT_DROPOFF_RADIUS_KM)


def test_pickup_and_dropoff_are_on_the_route():
    m = matching.find_matches(
        [_drive("Bernard", AIGLE_BERN_ROUTE, "2026-09-22T18:00")], VILLENEUVE, BERN, "2026-09-22T18:00"
    )[0]
    assert m["pickup"]["gap_km"] < 0.1  # the route runs through Villeneuve
    assert m["dropoff"]["gap_km"] < 3.0  # Bern station, ~2 km from the drive's end
    assert m["ride_km"] > 80


def test_radius_narrows_and_widens_the_search():
    # A route passing ~3 km from Riazzino.
    drive = _drive("Matteo", LOCARNO_CHUR_ROUTE, "2026-09-23T15:00")
    search = {"dropoff_radius_km": 10}
    assert matching.find_matches([drive], RIAZZINO, VERSAM, "2026-09-23T15:15", pickup_radius_km=2, **search) == []
    assert matching.find_matches([drive], RIAZZINO, VERSAM, "2026-09-23T15:15", pickup_radius_km=4, **search)


def test_time_tolerance_is_checked_at_the_pickup():
    # Aigle -> Bern in 100 min; Bulle is about halfway, ~50 min after departure.
    drive = _drive("Bernard", AIGLE_BERN_ROUTE, "2026-09-22T07:00", duration_min=100)
    bulle = (46.6193, 7.0569)
    pickup_time = matching.find_matches([drive], bulle, BERN, "2026-09-22T08:00")[0]["pickup"]["time"]
    assert "2026-09-22T07:40" <= pickup_time <= "2026-09-22T08:00"

    # Asked for 7:00 with 30 min tolerance: the drive leaves at 7:00 but only
    # reaches Bulle ~7:50, too late.
    assert matching.find_matches([drive], bulle, BERN, "2026-09-22T07:00", time_tolerance_min=30) == []
    assert matching.find_matches([drive], bulle, BERN, "2026-09-22T07:00", time_tolerance_min=60)


def test_full_drives_are_not_matched():
    drive = _drive("Bernard", AIGLE_BERN_ROUTE, "2026-09-22T18:00", seats_available=0)
    assert matching.find_matches([drive], VILLENEUVE, BERN, "2026-09-22T18:00") == []


def test_ranked_by_time_lost():
    # Same route; one at the requested time, one an hour later, one whose
    # route ends 4 km short of Bern (less convenient drop-off).
    on_time = _drive("On time", AIGLE_BERN_ROUTE, "2026-09-22T18:00")
    late = _drive("Late", AIGLE_BERN_ROUTE, "2026-09-22T19:00")
    short = _drive("Short", [*AIGLE_BERN_ROUTE[:-1], [7.40, 46.92]], "2026-09-22T18:00")
    matches = matching.find_matches([late, short, on_time], VILLENEUVE, BERN, "2026-09-22T18:00")
    assert [m["driver_name"] for m in matches] == ["On time", "Short", "Late"]


def test_short_trip_near_a_drive_both_ways_is_not_matched():
    # Both ends of a 3 km trip are within 5 km of the route, but getting to
    # and from the car would be longer than the trip itself.
    drive = _drive("Bernard", AIGLE_BERN_ROUTE, "2026-09-22T18:00")
    near_fribourg_west = (46.80, 7.10)
    near_fribourg_east = (46.79, 7.14)
    assert matching.find_matches([drive], near_fribourg_west, near_fribourg_east, "2026-09-22T18:00") == []


def test_drive_without_route_is_a_straight_line():
    drive = _drive("Straight", [[7.0, 46.5], [7.0, 47.0]], "2026-09-22T18:00")
    drive["route_geometry"] = None
    assert matching.find_matches([drive], (46.55, 7.02), (46.95, 6.99), "2026-09-22T18:00")


def test_could_pass_near_never_rules_out_a_real_match():
    drive = _drive("Matteo", LOCARNO_CHUR_ROUTE, "2026-09-23T15:00")
    route = matching.prepare_route(LOCARNO_CHUR_ROUTE, drive)
    drive["route_distance_m"] = route[-1][2] * 1000
    for lon, lat in LOCARNO_CHUR_ROUTE:
        assert matching.could_pass_near(drive, (lat + 0.03, lon), 5)  # ~3.3 km off the route
    assert not matching.could_pass_near(drive, (47.3769, 8.5417), 5)  # Zürich
