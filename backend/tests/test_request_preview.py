"""
GET /api/notifications/{id}/preview (routes/request_preview.py): what the
driver's map shows for a ride request they were told about. Isolated
database, offline.
"""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

import database as db
import demo_drives
import matching
import notify
from routes import request_preview

AIGLE_BERN = [
    [6.966930, 46.317162], [6.923530, 46.394032], [6.841900, 46.462800], [6.900000, 46.527000],
    [7.056900, 46.619300], [7.161900, 46.806500], [7.447398, 46.940830],
]
VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
FRIBOURG = {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619}
TOMORROW = date.today() + timedelta(days=1)


@pytest.fixture(autouse=True)
def no_demo_drivers(monkeypatch):
    monkeypatch.setattr(demo_drives, "DEMO_DRIVES", [])
    notify._demo_geometries.clear()


@pytest.fixture
def told(user_named, force_haversine):
    """A driver who made the trip before, told about Marie's request."""
    driver, other = user_named("Dora D."), user_named("Otto O.")
    day = date.today() - timedelta(days=7)
    (olon, olat), (dlon, dlat) = AIGLE_BERN[0], AIGLE_BERN[-1]
    db.create_drive(
        driver_name=driver["name"], origin_name="Aigle", origin_lat=olat, origin_lon=olon,
        destination_name="Bern", destination_lat=dlat, destination_lon=dlon,
        departure_time=f"{day}T18:00", seats_available=3, route_geometry=AIGLE_BERN,
        route_distance_m=100_000, route_duration_s=6000, route_source="osrm",
    )
    request = db.create_open_request("Marie M.", VEVEY, FRIBOURG, f"{TOMORROW}T18:30", 5, 5, 120)
    assert notify.notify_drivers(request) == ["Dora D."]
    [n] = notify.inbox("Dora D.")["notifications"]
    return {"driver": driver, "other": other, "id": n["id"], "request": request}


def _refused(user, notification_id):
    with pytest.raises(HTTPException) as exc:
        request_preview.preview_notification(notification_id, user)
    return exc.value.status_code, exc.value.detail["code"]


def test_the_recipient_gets_the_request_the_usual_route_and_the_points_on_it(told):
    out = request_preview.preview_notification(told["id"], told["driver"])
    assert out["request"]["origin_name"] == "Vevey" and out["request"]["destination_name"] == "Fribourg"
    usual = out["usual_trip"]
    assert (usual["origin"]["name"], usual["destination"]["name"]) == ("Aigle", "Bern")
    assert len(usual["route"]) >= 2 and all(len(p) == 2 for p in usual["route"])
    assert usual["pickup"]["time"] < usual["dropoff"]["time"]
    assert {"lat", "lon"} <= set(usual["pickup"]) and {"lat", "lon"} <= set(usual["dropoff"])
    assert usual["suggested_departure"]


def test_the_road_route_is_used_when_the_routing_service_answers(told, monkeypatch):
    road = [[6.97, 46.32], [7.0, 46.5], [7.2, 46.7], [7.45, 46.94]]
    monkeypatch.setattr(matching, "osrm_route", lambda points: {
        "distance_m": 100_000, "duration_s": 6000, "geometry": road,
    })
    usual = request_preview.preview_notification(told["id"], told["driver"])["usual_trip"]
    assert usual["route"][0] == [46.32, 6.97] and usual["route"][-1] == [46.94, 7.45]
    assert len(usual["route"]) > 2


def test_without_the_routing_service_the_route_is_a_straight_line(told):
    # force_haversine makes OSRM fail.
    usual = request_preview.preview_notification(told["id"], told["driver"])["usual_trip"]
    assert usual["route"] == [[46.317162, 6.96693], [46.94083, 7.447398]]


def test_another_user_gets_not_found(told):
    assert _refused(told["other"], told["id"]) == (404, "notification_not_found")
    assert _refused(told["driver"], told["id"] + 999) == (404, "notification_not_found")


def test_a_request_that_is_no_longer_open(told):
    with db.get_conn() as conn:
        conn.execute("UPDATE open_requests SET status = 'cancelled' WHERE id = ?", (told["request"]["id"],))
    assert _refused(told["driver"], told["id"]) == (404, "request_not_open")


def test_an_area_notification_has_only_the_request(user_named, force_haversine):
    import areas
    zoe = user_named("Zoe Z.")
    areas.add(zoe["id"], "Vevey", VEVEY["lat"], VEVEY["lon"], 5)
    request = db.create_open_request("Marie M.", VEVEY, FRIBOURG, f"{TOMORROW}T18:30", 5, 5, 120)
    assert notify.notify_drivers(request) == ["Zoe Z."]
    [n] = notify.inbox("Zoe Z.")["notifications"]
    out = request_preview.preview_notification(n["id"], zoe)
    assert set(out) == {"request"} and out["request"]["origin_name"] == "Vevey"


def test_a_notification_that_isnt_a_request_for_a_driver_is_not_found(user_named, force_haversine):
    marie = user_named("Marie M.")
    request = db.create_open_request("Marie M.", VEVEY, FRIBOURG, f"{TOMORROW}T18:30", 5, 5, 120)
    n = db.create_notification("Marie M.", "drive_for_passenger", request["id"], drive_ids=[])
    assert _refused(marie, n["id"]) == (404, "notification_not_found")
