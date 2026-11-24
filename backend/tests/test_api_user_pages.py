"""
Tests for the endpoints behind the per-user Driver and Passenger pages:
listing one driver's own drives, cancelling a drive, and a passenger's
recent searches. Isolated temp DB and haversine routing -- no network.
The route functions are called directly, as a given signed-in user;
test_auth.py covers the sessions and checks in front of them.
"""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

import database
from routes import drives as drive_routes
from routes import ride_requests as request_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}
BERN = {"name": "Bern", "lat": 46.9480, "lon": 7.4474}


def _post_drive(user, origin=LAUSANNE, destination=GENEVE, at="08:00"):
    return drive_routes.post_drive(drive_routes.DriveCreate(
        origin=origin,
        destination=destination,
        departure_time=f"{TOMORROW}T{at}",
    ), user)


def _search(passenger_name, origin, destination):
    db = database
    db.save_ride_request(
        passenger_name=passenger_name,
        origin_name=origin["name"], origin_lat=origin["lat"], origin_lon=origin["lon"],
        destination_name=destination["name"],
        destination_lat=destination["lat"], destination_lon=destination["lon"],
        requested_time=f"{TOMORROW}T08:00",
    )


def _session(user):
    return {"user": user, "session": {"is_demo": 0}, "extended": False}


def test_drives_filtered_by_driver(user_named, force_haversine):
    jean, sophie = user_named("Jean D."), user_named("Sophie M.")
    _post_drive(jean)
    _post_drive(sophie, origin=BERN)
    _post_drive(jean, destination=BERN, at="14:00")   # back from Genève by then

    mine = drive_routes.get_drives(_session(jean), mine=True)
    assert [d["destination_name"] for d in mine] == ["Genève", "Bern"]
    assert len(drive_routes.get_drives(None)) == 3


def test_own_drives_need_a_session(isolated_db):
    with pytest.raises(HTTPException) as exc:
        drive_routes.get_drives(None, mine=True)
    assert exc.value.status_code == 401


def test_driver_can_cancel_own_drive(user_named, force_haversine):
    jean = user_named("Jean D.")
    drive = _post_drive(jean)
    assert drive_routes.cancel_drive(drive["id"], jean) == {"deleted": drive["id"]}
    assert drive_routes.get_drives(None) == []


def test_cannot_cancel_someone_elses_drive(user_named, force_haversine):
    drive = _post_drive(user_named("Jean D."))
    with pytest.raises(HTTPException) as exc:
        drive_routes.cancel_drive(drive["id"], user_named("Sophie M."))
    assert exc.value.status_code == 403
    assert len(drive_routes.get_drives(None)) == 1


def test_cancel_missing_drive_is_404(user_named):
    with pytest.raises(HTTPException) as exc:
        drive_routes.cancel_drive(999, user_named("Jean D."))
    assert exc.value.status_code == 404


def test_recent_searches_newest_first_and_deduplicated(user_named):
    _search("Marie R.", LAUSANNE, GENEVE)
    _search("Marie R.", LAUSANNE, BERN)
    _search("Marie R.", LAUSANNE, GENEVE)  # repeat of the first search
    _search("Luca B.", BERN, GENEVE)       # someone else's

    recent = request_routes.get_ride_requests(user_named("Marie R."))
    assert [(r["origin_name"], r["destination_name"]) for r in recent] == [
        ("Lausanne", "Genève"),
        ("Lausanne", "Bern"),
    ]
