"""
meeting_points.py: names, walking time and onward times for a match's pickup
and drop-off, and the same through POST /api/match. Offline: OSRM and the
timetable are faked (see conftest.py).
"""
import time
from datetime import date, timedelta

import matching
import pytest
import meeting_points
import transit
from api import LatLon
from routes import match as match_routes
from test_api_match import AIGLE_BERN_ROUTE_GEOMETRY, DAY_1, _post_drive, _set_route

@pytest.fixture(autouse=True)
def fresh_misses():
    meeting_points._misses.clear()
    meeting_points._paused_until = 0.0
    yield
    meeting_points._misses.clear()
    meeting_points._paused_until = 0.0


DEST = LatLon(name="Somewhere", lat=46.95, lon=7.44)
START = LatLon(name="Start", lat=46.30, lon=6.90)


def _match(pickup_gap=1.0, dropoff_gap=1.5, dropoff=(46.96, 7.45), dropoff_time="2030-01-01T07:50", arrival=None):
    m = {
        "pickup": {"lat": 46.31, "lon": 6.91, "gap_km": pickup_gap, "time": "2030-01-01T07:00"},
        "dropoff": {"lat": dropoff[0], "lon": dropoff[1], "gap_km": dropoff_gap, "time": dropoff_time},
        "last_mile_transit": None,
    }
    if arrival:
        m["last_mile_transit"] = {"from_station": "A", "to_station": "B", "connections": [{"arrival": arrival}]}
    return m


def test_onward_times_from_osrm_table(monkeypatch):
    calls = []

    def table(sources, target):
        calls.append((sources, target))
        return [(1800.0, 240.0)]

    monkeypatch.setattr(matching, "osrm_table", table)
    m = _match(arrival="2030-01-01T07:59:10")
    meeting_points.add_to([m], START, DEST)
    onward = m["dropoff"]["onward"]
    assert len(calls) == 1
    assert onward["car_min"] == 4
    assert onward["walk_min"] == 22  # 1.8 km road distance at 12 min/km
    assert onward["transit_min"] == 10  # 07:50 -> 07:59:10, rounded up
    assert onward["source"] == "osrm"
    assert m["pickup"]["walk_min"] == 16  # 1.0 km straight line * 1.3 at 12 min/km


def test_osrm_down_falls_back_to_estimates(monkeypatch):
    monkeypatch.setattr(matching, "osrm_table", lambda sources, target: None)
    m = _match()
    meeting_points.add_to([m], START, DEST)
    onward = m["dropoff"]["onward"]
    assert onward["source"] == "estimate"
    assert onward["car_min"] >= 1 and onward["walk_min"] > onward["car_min"]
    assert onward["transit_min"] is None  # no last-mile leg


def test_osrm_table_raising_does_not_fail(monkeypatch):
    def boom(sources, target):
        raise RuntimeError("down")

    monkeypatch.setattr(matching, "osrm_table", boom)
    m = _match()
    meeting_points.add_to([m], START, DEST)
    assert m["dropoff"]["onward"]["source"] == "estimate"


def test_point_at_the_destination_gets_no_onward(monkeypatch):
    calls = []
    monkeypatch.setattr(matching, "osrm_table", lambda s, t: calls.append(s))
    m = _match(pickup_gap=0.1, dropoff_gap=0.2)
    meeting_points.add_to([m], START, DEST)
    assert m["dropoff"]["onward"] is None
    assert m["pickup"]["walk_min"] is None
    assert calls == []  # nothing to ask OSRM


def test_names_from_nearby_stop_only(fake_transit, monkeypatch):
    monkeypatch.setattr(matching, "osrm_table", lambda s, t: None)
    fake_transit.add_station("Bern, Bahnhof", 46.9601, 7.4501)  # ~13 m from the drop-off
    m = _match()
    meeting_points.add_to([m], START, DEST)
    assert m["dropoff"]["name"] == "Bern, Bahnhof"
    assert m["pickup"]["name"] is None  # that stop is 60+ km away


def test_name_lookups_are_limited_and_deduplicated(fake_transit, monkeypatch):
    monkeypatch.setattr(matching, "osrm_table", lambda s, t: None)
    fake_transit.add_station("Stop", 46.31, 6.91)
    matches = [_match() for _ in range(20)]  # the same two points twenty times
    meeting_points.add_to(matches, START, DEST)
    assert len(fake_transit.nearest_station_calls) == 2
    assert matches[19]["pickup"]["name"] == "Stop"

    fake_transit.nearest_station_calls.clear()
    transit._cache.clear()
    meeting_points._misses.clear()
    many = []
    for i in range(20):
        m = _match(dropoff=(46.9 + i * 0.01, 7.4))
        many.append(m)
    meeting_points.add_to(many, START, DEST)
    assert len(fake_transit.nearest_station_calls) <= meeting_points.MAX_NAME_LOOKUPS


def test_bookable_rides_are_named_first(fake_transit, monkeypatch):
    monkeypatch.setattr(matching, "osrm_table", lambda s, t: None)
    monkeypatch.setattr(meeting_points, "MAX_NAME_LOOKUPS", 2)
    fake_transit.add_station("Stop", 46.31, 6.91)  # at every pickup
    matches = [_match(dropoff=(46.9 + i * 0.01, 7.4)) for i in range(10)]
    matches[9]["bookable"] = True  # the last of the list is the one that can be booked
    meeting_points.add_to(matches, START, DEST)
    asked = {(round(lat, 4), round(lon, 4)) for lat, lon in fake_transit.nearest_station_calls}
    assert asked == {(46.31, 6.91), (46.99, 7.4)}  # its pickup and its drop-off, nothing else
    assert matches[9]["pickup"]["name"] == "Stop"


def test_names_are_looked_up_side_by_side(monkeypatch):
    monkeypatch.setattr(matching, "osrm_table", lambda s, t: None)

    def slow(lat, lon):
        time.sleep(0.2)
        return {"name": f"Stop {lat}", "distance_m": 10}

    monkeypatch.setattr(transit, "nearest_named_station", slow)
    matches = [_match(dropoff=(46.9 + i * 0.01, 7.4)) for i in range(7)]  # 8 different points
    started = time.monotonic()
    meeting_points.add_to(matches, START, DEST)
    assert time.monotonic() - started < 1.2  # one after another: 1.6 s
    assert all(m["dropoff"]["name"] for m in matches)


def test_pauses_when_the_service_does_not_answer_in_time(monkeypatch):
    monkeypatch.setattr(matching, "osrm_table", lambda s, t: None)
    monkeypatch.setattr(meeting_points, "NAME_BUDGET_S", 0.05)
    calls = []

    def hangs(lat, lon):
        calls.append((lat, lon))
        time.sleep(0.4)
        return {"name": "Late", "distance_m": 10}

    monkeypatch.setattr(transit, "nearest_named_station", hangs)
    m = _match()
    started = time.monotonic()
    meeting_points.add_to([m], START, DEST)
    assert time.monotonic() - started < 0.35  # the search didn't wait for it
    assert m["pickup"]["name"] is None and m["dropoff"]["onward"] is not None
    time.sleep(0.5)  # let the two lookups end
    asked = len(calls)
    other = _match(dropoff=(46.80, 7.30))
    meeting_points.add_to([other], START, DEST)
    assert len(calls) == asked  # paused: the service isn't asked again


def test_name_lookup_raising_does_not_fail(monkeypatch):
    monkeypatch.setattr(matching, "osrm_table", lambda s, t: None)
    meeting_points._misses.clear()

    def boom(lat, lon):
        raise RuntimeError("boom")

    monkeypatch.setattr(transit, "nearest_named_station", boom)
    m = _match()
    meeting_points.add_to([m], START, DEST)
    assert m["pickup"]["name"] is None and m["dropoff"]["onward"] is not None


def test_osrm_table_parses_answer_and_failure(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"code": "Ok", "distances": [[1500.0], [None]], "durations": [[120.0], [None]]}

    monkeypatch.setattr(matching._session, "get", lambda *a, **k: Resp())
    assert matching.osrm_table([(46.9, 7.4), (46.8, 7.4)], (46.95, 7.44)) == [(1500.0, 120.0), None]

    def refuse(*a, **k):
        raise matching.requests.ConnectionError()

    monkeypatch.setattr(matching._session, "get", refuse)
    assert matching.osrm_table([(46.9, 7.4)], (46.95, 7.44)) is None


def test_api_match_carries_the_structured_data(isolated_db, force_haversine, fake_transit):
    meeting_points._misses.clear()
    fake_transit.add_station("Villeneuve VD, gare", 46.3944, 6.9240)
    drive = _post_drive(
        "Bernard",
        origin=("Rue du Rhône 9 1860 Aigle", 46.317161560058594, 6.966929912567139),
        destination=("Hallwylstrasse 4 3005 Bern", 46.94083023071289, 7.4473981857299805),
        departure_time=f"{DAY_1}T18:00",
    )
    _set_route(drive, AIGLE_BERN_ROUTE_GEOMETRY)
    match = match_routes.match_ride(match_routes.MatchRequest(
        origin={"name": "Villeneuve VD", "lat": 46.3940315246582, "lon": 6.923530101776123},
        destination={"name": "Bern (BE)", "lat": 46.954559326171875, "lon": 7.420684814453125},
        requested_time=f"{DAY_1}T18:00",
    ))["driver_matches"][0]
    assert match["pickup"]["name"] == "Villeneuve VD, gare"
    assert match["dropoff"]["onward"]["car_min"] >= 1
    assert match["dropoff"]["onward"]["source"] == "estimate"  # OSRM faked down
