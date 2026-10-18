"""
Shared pytest fixtures for the backend test suite.

Everything here is designed to run the matching/dropoff algorithms fully
offline and deterministically: OSRM and transport.opendata.ch are both free,
unauthenticated, rate-limited demo services (see matching.py / transit.py
docstrings), so tests must never depend on reaching them -- a slow network,
a rate limit, or an outage would otherwise make CI flaky for reasons that
have nothing to do with the code under test.
"""
import os
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import database as db  # noqa: E402

# `main.py` calls db.init_db() at import time, against whatever db.DB_PATH
# currently points to. Repoint it to a throwaway file *before* anything in
# this test session can trigger that import, so tests can never create
# tables in (let alone write to) the real rideshare.db in the repo root.
db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="rideshare-tests-"), "_unused.db")

import matching  # noqa: E402
import pytest  # noqa: E402
import transit  # noqa: E402


@pytest.fixture
def force_haversine(monkeypatch):
    """
    Force matching.route_with_fallback() to always use the haversine
    fallback instead of calling the real OSRM demo server. Distances become
    pure straight-line math on whatever coordinates a test supplies --
    deterministic and network-free -- at the cost of not reflecting real
    road curvature (fine for algorithm tests; not a substitute for the
    manual/live verification that established the real-world numbers these
    tests are modeled on).
    """
    monkeypatch.setattr(matching, "osrm_route", lambda points: None)


class FakeTransit:
    """
    Drop-in replacement for the pieces of transit.py that call the real
    transport.opendata.ch API. `stations` is a list of {name, lat, lon}
    dicts; nearest_named_station finds whichever is geographically closest
    to a query point (like the real API), so callers don't need an exact
    coordinate match. `place_stations` maps a town name (as the user
    picked it) -> the station station_for_place should return for it.
    `connections` maps (from_name, to_name) -> a list of connection dicts
    (or leave a pair out / map it to [] for "no route found").
    """

    def __init__(self):
        self.stations = []
        self.place_stations = {}
        self.connections = {}
        self.connection_calls = []
        self.nearest_station_calls = []

    def add_station(self, name, lat, lon):
        self.stations.append({"name": name, "lat": lat, "lon": lon})

    def add_place_station(self, place_name, name, lat, lon):
        self.place_stations[place_name] = {"name": name, "lat": lat, "lon": lon}

    def station_for_place(self, place_name, lat, lon):
        station = self.place_stations.get(place_name)
        if not station:
            return None
        distance_m = matching.haversine_km(lat, lon, station["lat"], station["lon"]) * 1000.0
        return {**station, "distance_m": distance_m}

    def set_connections(self, from_name, to_name, connections):
        self.connections[(from_name, to_name)] = connections

    def nearest_named_station(self, lat, lon):
        self.nearest_station_calls.append((lat, lon))
        if not self.stations:
            return None
        closest = min(
            self.stations,
            key=lambda s: matching.haversine_km(lat, lon, s["lat"], s["lon"]),
        )
        distance_m = matching.haversine_km(lat, lon, closest["lat"], closest["lon"]) * 1000.0
        return {"name": closest["name"], "lat": closest["lat"], "lon": closest["lon"], "distance_m": distance_m}

    def get_connections(self, from_station, to_station, when_iso=None, limit=3):
        self.connection_calls.append((from_station, to_station))
        return self.connections.get((from_station, to_station), [])


@pytest.fixture
def fake_transit(monkeypatch):
    fake = FakeTransit()
    monkeypatch.setattr(transit, "nearest_named_station", fake.nearest_named_station)
    monkeypatch.setattr(transit, "station_for_place", fake.station_for_place)
    monkeypatch.setattr(transit, "get_connections", fake.get_connections)
    return fake


@pytest.fixture
def dense_dropoff_sampling(monkeypatch):
    """
    dropoff.py samples a route every SAMPLE_STEP_KM (10km in production) to
    keep the number of candidate stations -- and thus network calls -- down
    on a real, densely-pointed OSRM polyline. Test fixtures below use short,
    hand-written polylines (real coordinates, but far fewer points than a
    real OSRM route), where a 10km step can skip past a landmark vertex
    entirely between two samples. Shrinking the step for these tests makes
    every hand-placed vertex get sampled, independent of that production
    tuning value.
    """
    import dropoff
    monkeypatch.setattr(dropoff, "SAMPLE_STEP_KM", 2.0)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """
    Point database.py at a fresh, empty sqlite file for the duration of one
    test, so drives/ride_requests written by a test never touch (or depend
    on) the real rideshare.db or another test's data.
    """
    db_path = str(tmp_path / "test_rideshare.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    return db
