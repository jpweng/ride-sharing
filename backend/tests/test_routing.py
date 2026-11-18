"""
Tests for the routing client (matching.osrm_route): the configurable OSRM
URL, retries, the circuit breaker and the straight-line fallback, against a
small local stand-in for OSRM -- no network. Also: demo drives made while
routing was down get their road route once it's back.
"""
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import database as db
import demo_drives
import matching

ROAD = [[7.4474, 46.9480], [7.6, 47.1], [8.5417, 47.3769]]


class FakeOsrm:
    """Answers /route/v1/driving/... with `status` and `body`; counts calls."""

    def __init__(self):
        self.status = 200
        self.body = {"code": "Ok", "routes": [{"distance": 125000.0, "duration": 5400.0,
                                               "geometry": {"type": "LineString", "coordinates": ROAD}}]}
        self.paths = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.paths.append(self.path)
                payload = json.dumps(fake.body).encode()
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_osrm(monkeypatch):
    fake = FakeOsrm()
    monkeypatch.setattr(matching, "OSRM_URL", fake.url)
    # A fresh breaker per test, closed.
    monkeypatch.setattr(matching, "_breaker", matching._CircuitBreaker(matching.OSRM_MAX_FAILURES, 60.0))
    monkeypatch.setattr(demo_drives, "ROUTING_PAUSE_S", 0)
    yield fake
    fake.close()


BERN, ZURICH = (46.9480, 7.4474), (47.3769, 8.5417)


def test_routes_come_from_the_configured_server(fake_osrm):
    route = matching.route_with_fallback([BERN, ZURICH])

    assert route == {"distance_m": 125000.0, "duration_s": 5400.0, "geometry": ROAD, "source": "osrm"}
    assert fake_osrm.paths[0].startswith("/route/v1/driving/7.447400,46.948000;8.541700,47.376900?")


def test_no_route_is_an_answer_not_a_failure(fake_osrm):
    fake_osrm.status = 400
    fake_osrm.body = {"code": "NoRoute", "message": "Impossible route between points"}

    for _ in range(matching.OSRM_MAX_FAILURES + 1):
        assert matching.route_with_fallback([BERN, ZURICH])["source"] == "haversine"

    # No retries, and the server is still asked every time.
    assert len(fake_osrm.paths) == matching.OSRM_MAX_FAILURES + 1


def test_server_errors_are_retried(fake_osrm):
    fake_osrm.status = 503

    assert matching.osrm_route([BERN, ZURICH]) is None
    assert len(fake_osrm.paths) == 1 + matching.OSRM_RETRIES


def test_a_failing_server_is_left_alone_for_a_while(fake_osrm):
    fake_osrm.status = 503
    for _ in range(matching.OSRM_MAX_FAILURES):
        matching.osrm_route([BERN, ZURICH])
    asked = len(fake_osrm.paths)

    # Back up, but not asked again until the cooldown is over.
    fake_osrm.status = 200
    route = matching.route_with_fallback([BERN, ZURICH])
    assert route["source"] == "haversine"
    assert len(fake_osrm.paths) == asked

    matching._breaker.open_until = 0.0
    assert matching.route_with_fallback([BERN, ZURICH])["source"] == "osrm"


def test_unreachable_server_falls_back_to_a_straight_line(monkeypatch):
    # Nothing listens on port 9 (discard) locally: connection refused.
    monkeypatch.setattr(matching, "OSRM_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(matching, "_breaker", matching._CircuitBreaker(matching.OSRM_MAX_FAILURES, 60.0))

    route = matching.route_with_fallback([BERN, ZURICH])

    assert route["source"] == "haversine"
    assert route["geometry"] == [[7.4474, 46.9480], [8.5417, 47.3769]]


WED_NOON = datetime(2026, 10, 21, 12, 0)


def test_demo_drives_get_their_road_route_once_routing_is_back(isolated_db, fake_osrm, monkeypatch):
    monkeypatch.setattr(demo_drives, "DEMO_DRIVES", [
        s for s in demo_drives.DEMO_DRIVES if s["driver_name"] == "Jean Dupont"
    ])
    fake_osrm.status = 503
    deleted, created = demo_drives.refresh(WED_NOON)
    assert created > 0
    drives = db.list_drives(upcoming_only=False, with_geometry=False)
    assert {d["route_source"] for d in drives} == {"haversine"}

    # Still down: nothing changes.
    assert demo_drives.refresh(WED_NOON) == (0, 0)

    fake_osrm.status = 200
    matching._breaker.open_until = 0.0
    assert demo_drives.refresh(WED_NOON) == (created, created)
    drives = db.list_drives(upcoming_only=False, with_geometry=False)
    assert {d["route_source"] for d in drives} == {"osrm"}
    # And then they stay.
    assert demo_drives.refresh(WED_NOON) == (0, 0)
