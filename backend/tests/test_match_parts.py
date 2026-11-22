"""
A search in parts (routes/match_parts.py): the drives first, without waiting
for the timetable (/api/match with "drives_only"), then public transport
alone and the combined trips, each on its own. And the timetable cache
asking once when the same question comes twice at the same time.
Isolated database, offline: the timetable is faked.
"""
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import database as db
import pytest
import transit
from fastapi.testclient import TestClient
from main import app

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
DAY = (date.today() + timedelta(days=2)).isoformat()

# A drive due north along longitude 7.0, A (46.0) to B (46.9), 100 km in 100
# minutes at 08:00. The passenger starts near A and goes 100 km beyond B.
A, B = (46.0, 7.0), (46.9, 7.0)
START = {"name": "Start", "lat": 46.05, "lon": 7.0}
END = {"name": "End", "lat": 47.8, "lon": 7.0}
NEAR_B = {"name": "Near B", "lat": 46.85, "lon": 7.0}


def _connection(leaves, arrives):
    return {"departure": f"{DAY}T{leaves}:00+0100", "arrival": f"{DAY}T{arrives}:00+0100", "duration": None,
            "transfers": 0, "products": ["IC"], "legs": [], "operators": [], "booking_url": None}


@pytest.fixture
def client(isolated_db, fake_transit, force_haversine):
    db.create_drive(
        driver_name="Driver", origin_name="A", origin_lat=A[0], origin_lon=A[1],
        destination_name="B", destination_lat=B[0], destination_lon=B[1],
        departure_time=f"{DAY}T08:00", seats_available=3, route_geometry=[[A[1], A[0]], [B[1], B[0]]],
        route_distance_m=100_000, route_duration_s=6000, route_source="osrm",
    )
    fake_transit.add_station("Start, station", START["lat"], START["lon"])
    fake_transit.add_station("B, station", B[0], B[1])
    fake_transit.add_station("End, station", END["lat"], END["lon"])
    fake_transit.set_connections("B, station", "End, station", [_connection("10:00", "11:00")])
    fake_transit.set_connections("Start, station", "End, station", [_connection("08:10", "10:55")])
    fake_transit.set_connections("Start, station", "B, station", [_connection("08:10", "09:30")])
    client = TestClient(app)
    client.fake_transit = fake_transit
    return client


def _search(to=END, **more):
    return {"origin": START, "destination": to, "requested_time": f"{DAY}T08:00", **more}


def test_the_parts_together_are_the_whole_search(client):
    whole = client.post("/api/match", json=_search()).json()
    assert whole["driver_matches"] == [] and len(whole["combined_matches"]) == 1 and whole["transit_option"]

    drives = client.post("/api/match", json=_search(drives_only=True)).json()
    assert drives["driver_matches"] == [] and drives["search_radius_km"] == whole["search_radius_km"]
    assert drives["combined_matches"] == [] and drives["transit_option"] is None
    assert client.post("/api/match/transit", json=_search()).json() == {"transit_option": whole["transit_option"]}
    combined = client.post("/api/match/combined", json=_search(exclude_ids=[])).json()
    assert combined == {"combined_matches": whole["combined_matches"]}
    [trip] = combined["combined_matches"]
    assert trip["vs_transit_min"] == 10 and trip["bookable"] is True and trip["price_chf"] is not None
    assert trip["driver_name"] is None   # not signed in: who drives isn't said


def test_the_drives_alone_do_not_wait_for_the_whole_trips_public_transport(client):
    # The drive passes near both ends of this one: a match.
    answer = client.post("/api/match", json=_search(to=NEAR_B, drives_only=True)).json()
    [match] = answer["driver_matches"]
    assert match["driver_name"] is None and answer["transit_option"] is None
    assert ("Start, station", "B, station") not in client.fake_transit.connection_calls
    # The whole search asks for it.
    assert client.post("/api/match", json=_search(to=NEAR_B)).json()["transit_option"]["to_station"] == "B, station"
    assert ("Start, station", "B, station") in client.fake_transit.connection_calls


def test_the_drives_the_search_found_are_not_proposed_for_a_part_of_the_way(client):
    [trip] = client.post("/api/match/combined", json=_search()).json()["combined_matches"]
    assert client.post("/api/match/combined", json=_search(exclude_ids=[trip["id"]])).json() == {"combined_matches": []}


@pytest.mark.parametrize("path", ["/api/match/transit", "/api/match/combined"])
def test_a_part_wants_a_time_too(client, path):
    answer = client.post(path, json={**_search(), "requested_time": "soon"})
    assert answer.status_code == 400 and answer.json()["detail"]["code"] == "invalid_time"


def test_the_same_question_at_the_same_time_is_asked_once():
    cache = transit._TtlCache(10)
    asked, go = [], threading.Event()

    def ask():
        asked.append(1)
        go.wait(5)
        return ["answer"]

    answers = []
    threads = [threading.Thread(target=lambda: answers.append(cache.fetch("key", ask, 60))) for _ in range(4)]
    for thread in threads:
        thread.start()
    time.sleep(0.05)   # all four are asking, or waiting for the one that is
    go.set()
    for thread in threads:
        thread.join(5)
    assert answers == [["answer"]] * 4 and len(asked) == 1
    assert cache.fetch("key", ask, 60) == ["answer"] and len(asked) == 1   # and it is kept


def test_a_question_that_failed_is_asked_again():
    cache = transit._TtlCache(10)

    def down():
        raise ValueError("down")

    with pytest.raises(ValueError):
        cache.fetch("key", down, 60)
    assert cache.fetch("key", lambda: "answer", 60) == "answer"


def test_the_page_shows_the_drives_first():
    app_js = (FRONTEND / "app.js").read_text()
    assert 'postJson("/api/match", { ...payload, drives_only: true })' in app_js
    assert 'later("/api/match/transit", payload, "transit_option", null)' in app_js
    assert 'exclude_ids: data.driver_matches.map((m) => m.id)' in app_js
    # A part that arrives fills its own place: the drives' cards aren't drawn again.
    assert 'if (!arrived || !data.driver_matches.length) renderMatches(' in app_js
    assert 'data.combined_matches === STILL_SEARCHING' in (FRONTEND / "combined.js").read_text()
