"""
Tests for the endpoints behind the per-user Driver and Passenger pages:
listing one driver's own drives, cancelling a drive, and a passenger's
recent searches. Isolated temp DB and haversine routing -- no network.
"""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

import main

TOMORROW = (date.today() + timedelta(days=1)).isoformat()

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}
BERN = {"name": "Bern", "lat": 46.9480, "lon": 7.4474}


def _post_drive(driver_name, origin=LAUSANNE, destination=GENEVE):
    return main.post_drive(main.DriveCreate(
        driver_name=driver_name,
        origin=origin,
        destination=destination,
        departure_time=f"{TOMORROW}T08:00",
    ))


def _search(passenger_name, origin, destination):
    db = main.db
    db.save_ride_request(
        passenger_name=passenger_name,
        origin_name=origin["name"], origin_lat=origin["lat"], origin_lon=origin["lon"],
        destination_name=destination["name"],
        destination_lat=destination["lat"], destination_lon=destination["lon"],
        requested_time=f"{TOMORROW}T08:00",
    )


def test_drives_filtered_by_driver(isolated_db, force_haversine):
    _post_drive("Jean Dupont")
    _post_drive("Sophie Meier", origin=BERN)
    _post_drive("Jean Dupont", destination=BERN)

    mine = main.get_drives(driver_name="Jean Dupont")
    assert [d["destination_name"] for d in mine] == ["Genève", "Bern"]
    assert len(main.get_drives()) == 3


def test_driver_can_cancel_own_drive(isolated_db, force_haversine):
    drive = _post_drive("Jean Dupont")
    assert main.cancel_drive(drive["id"], driver_name="Jean Dupont") == {"deleted": drive["id"]}
    assert main.get_drives() == []


def test_cannot_cancel_someone_elses_drive(isolated_db, force_haversine):
    drive = _post_drive("Jean Dupont")
    with pytest.raises(HTTPException) as exc:
        main.cancel_drive(drive["id"], driver_name="Sophie Meier")
    assert exc.value.status_code == 403
    assert len(main.get_drives()) == 1


def test_cancel_missing_drive_is_404(isolated_db):
    with pytest.raises(HTTPException) as exc:
        main.cancel_drive(999, driver_name="Jean Dupont")
    assert exc.value.status_code == 404


def test_recent_searches_newest_first_and_deduplicated(isolated_db):
    _search("Marie Rochat", LAUSANNE, GENEVE)
    _search("Marie Rochat", LAUSANNE, BERN)
    _search("Marie Rochat", LAUSANNE, GENEVE)  # repeat of the first search
    _search("Luca Bianchi", BERN, GENEVE)       # someone else's

    recent = main.get_ride_requests(passenger_name="Marie Rochat")
    assert [(r["origin_name"], r["destination_name"]) for r in recent] == [
        ("Lausanne", "Genève"),
        ("Lausanne", "Bern"),
    ]
