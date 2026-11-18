"""
Driver areas (areas.py, routes/areas.py): saving, listing, changing and
removing a driver's areas, the limits, and what happens to them when the
account goes. The route functions are called directly, as a given signed-in
user; test_auth.py covers the sessions and checks in front of them.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import areas
import database as db
import main
from routes import areas as area_routes

ZUG = {"name": "Zug", "lat": 47.1662, "lon": 8.5155}
RAPPERSWIL = {"name": "Rapperswil", "lat": 47.2266, "lon": 8.8184}


def _add(user, place=ZUG, **fields):
    return area_routes.add_area(area_routes.AreaCreate(place=place, **fields), user)


def _code(exc):
    return exc.value.status_code, exc.value.detail["code"]


def test_no_areas_at_first_with_the_limits(user_named):
    assert area_routes.get_areas(user_named("Jean D.")) == {
        "areas": [],
        "max_areas": 5,
        "radius_options_km": [5, 10, 20, 30],
        "default_radius_km": 10,
    }


def test_add_an_area_with_the_default_radius(user_named):
    jean = user_named("Jean D.")
    area = _add(jean)
    assert (area["name"], area["lat"], area["lon"], area["radius_km"]) == ("Zug", 47.1662, 8.5155, 10)
    assert area["user_id"] == jean["id"]
    assert area_routes.get_areas(jean)["areas"] == [area]


def test_areas_listed_oldest_first_with_their_radius(user_named):
    jean = user_named("Jean D.")
    _add(jean, radius_km=20)
    _add(jean, RAPPERSWIL, radius_km=5)
    assert [(a["name"], a["radius_km"]) for a in area_routes.get_areas(jean)["areas"]] == [
        ("Zug", 20), ("Rapperswil", 5),
    ]


def test_each_user_sees_only_their_own(user_named):
    jean, sophie = user_named("Jean D."), user_named("Sophie M.")
    _add(jean)
    assert area_routes.get_areas(sophie)["areas"] == []


def test_at_most_five_areas(user_named):
    jean = user_named("Jean D.")
    for _ in range(areas.MAX_AREAS):
        _add(jean)
    with pytest.raises(HTTPException) as exc:
        _add(jean)
    assert _code(exc) == (400, "too_many_areas")
    assert len(area_routes.get_areas(jean)["areas"]) == areas.MAX_AREAS
    # Removing one makes room again.
    area_routes.remove_area(area_routes.get_areas(jean)["areas"][0]["id"], jean)
    _add(jean, RAPPERSWIL)


@pytest.mark.parametrize("radius_km", [0, 7, 31, -10])
def test_radius_must_be_one_of_the_options(radius_km):
    with pytest.raises(ValidationError):
        area_routes.AreaCreate(place=ZUG, radius_km=radius_km)
    with pytest.raises(ValidationError):
        area_routes.AreaUpdate(radius_km=radius_km)


@pytest.mark.parametrize("place", [
    {**ZUG, "lat": 91}, {**ZUG, "lon": -181}, {**ZUG, "name": ""}, {**ZUG, "name": "x" * 201},
])
def test_place_must_be_a_named_point_on_earth(place):
    with pytest.raises(ValidationError):
        area_routes.AreaCreate(place=place)


def test_change_the_radius(user_named):
    jean = user_named("Jean D.")
    area = _add(jean)
    changed = area_routes.update_area(area["id"], area_routes.AreaUpdate(radius_km=30), jean)
    assert changed == {**area, "radius_km": 30}
    assert area_routes.get_areas(jean)["areas"] == [changed]


def test_remove_an_area(user_named):
    jean = user_named("Jean D.")
    area = _add(jean)
    assert area_routes.remove_area(area["id"], jean) == {"deleted": area["id"]}
    assert area_routes.get_areas(jean)["areas"] == []
    with pytest.raises(HTTPException) as exc:
        area_routes.remove_area(area["id"], jean)
    assert _code(exc) == (404, "area_not_found")


def test_someone_elses_area_is_not_found(user_named):
    jean, sophie = user_named("Jean D."), user_named("Sophie M.")
    area = _add(jean)
    with pytest.raises(HTTPException) as exc:
        area_routes.update_area(area["id"], area_routes.AreaUpdate(radius_km=30), sophie)
    assert _code(exc) == (404, "area_not_found")
    with pytest.raises(HTTPException) as exc:
        area_routes.remove_area(area["id"], sophie)
    assert _code(exc) == (404, "area_not_found")
    assert area_routes.get_areas(jean)["areas"] == [area]


def test_deleting_the_account_deletes_its_areas(user_named):
    jean, sophie = user_named("Jean D."), user_named("Sophie M.")
    _add(jean)
    _add(jean, RAPPERSWIL)
    kept = _add(sophie)
    assert db.delete_account(jean["id"])["areas"] == 2
    assert areas.list_for(jean["id"]) == []
    assert areas.list_for(sophie["id"]) == [kept]


def test_a_name_without_an_account_goes_with_its_areas(user_named):
    # Names without an email are deleted once nothing else references them
    # (maintenance.py); an area doesn't keep one alive, nor block its deletion.
    jean = user_named("Jean D.")
    _add(jean)
    assert db.delete_inactive_users() == 1
    assert areas.list_for(jean["id"]) == []


@pytest.mark.parametrize("method, path, body", [
    ("get", "/api/areas", None),
    ("post", "/api/areas", {"place": ZUG, "radius_km": 10}),
    ("put", "/api/areas/1", {"radius_km": 10}),
    ("delete", "/api/areas/1", None),
])
def test_areas_need_a_session(isolated_db, method, path, body):
    resp = TestClient(main.app, base_url="https://testserver").request(method, path, json=body)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "not_signed_in"
