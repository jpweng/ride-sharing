"""
Prices through the API: the band the Driver form asks for, the driver's
price checked and stored when posting, and the prices drives and matches
are listed with. Isolated temp DB and haversine routing -- no network.
"""
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

import database as db
import main
import pricing

TOMORROW = (date.today() + timedelta(days=1)).isoformat()

# ~24 km apart in a straight line (the haversine fallback's "road").
VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
BULLE = {"name": "Bulle", "lat": 46.6193, "lon": 7.0569}


def _post(price=None, seats=3, origin=VEVEY, destination=BULLE):
    return main.post_drive(main.DriveCreate(
        driver_name="Jean Dupont", origin=origin, destination=destination,
        departure_time=f"{TOMORROW}T08:00", seats_available=seats, price_chf=price,
    ))


def _band():
    return main.get_price_band(VEVEY["lat"], VEVEY["lon"], BULLE["lat"], BULLE["lon"])


def test_price_band_for_the_driver_form(isolated_db, force_haversine):
    band = _band()
    assert set(band) >= {"min_chf", "max_chf", "default_chf", "warn_above_chf", "train_fare", "ceilings_chf"}
    assert band["min_chf"] < band["default_chf"] <= band["warn_above_chf"] < band["max_chf"]
    assert band["ceilings_chf"][:2] == [pricing.ceiling_chf(band["km"], 1), pricing.ceiling_chf(band["km"], 2)]
    assert len(band["ceilings_chf"]) == 8


def test_price_band_needs_two_places(isolated_db, force_haversine):
    with pytest.raises(HTTPException) as exc:
        main.get_price_band(46.5, 6.6, 46.5, 6.6)
    assert exc.value.detail["code"] == "no_distance"


def test_drivers_price_is_stored_and_shown(isolated_db, force_haversine):
    band = _band()
    drive = _post(price=band["max_chf"], seats=4)
    assert drive["driver_price_chf"] == band["max_chf"]
    assert drive["price_chf"] == band["max_chf"]
    # Falls for each passenger as more join.
    assert drive["price_by_passengers"] == sorted(drive["price_by_passengers"], reverse=True)
    assert drive["price_by_passengers"][1] < drive["price_chf"]
    assert drive["train_fare"]["approximate"] is True
    assert main.get_drive(drive["id"])["price_chf"] == band["max_chf"]
    assert main.get_drives()[0]["driver_price_chf"] == band["max_chf"]


def test_without_a_price_the_default_is_stored(isolated_db, force_haversine):
    drive = _post()
    assert drive["driver_price_chf"] == _band()["default_chf"]


@pytest.mark.parametrize("delta,code", [(0.10, "price_too_high"), (-1000, "price_too_low")])
def test_prices_outside_the_band_are_rejected(isolated_db, force_haversine, delta, code):
    band = _band()
    price = band["max_chf"] + delta if delta > 0 else band["min_chf"] - 0.10
    with pytest.raises(HTTPException) as exc:
        _post(price=round(price, 2))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == code
    assert main.get_drives() == []


def test_match_shows_the_passengers_price_and_the_train_fare(isolated_db, force_haversine, fake_transit):
    _post(price=_band()["max_chf"])
    result = main.match_ride(main.MatchRequest(
        passenger_name="Marie",
        origin=VEVEY,
        destination=BULLE,
        requested_time=f"{TOMORROW}T08:00",
    ))
    [match] = result["driver_matches"]
    assert match["price_chf"] <= pricing.ceiling_chf(match["ride_km"], 1)
    assert match["price_chf"] <= match["train_fare"]["full_chf"]
    assert match["train_fare"]["tariff_km"] == round(match["ride_km"], 1)


def test_transit_option_has_the_train_fare(isolated_db, force_haversine, fake_transit):
    fake_transit.add_station("Vevey", 46.4628, 6.8419)
    fake_transit.add_station("Bulle", 46.6193, 7.0569)
    fake_transit.set_connections("Vevey", "Bulle", [{"departure": f"{TOMORROW}T08:00:00", "arrival": f"{TOMORROW}T08:40:00",
                                                     "duration": "00d00:40:00", "transfers": 1, "products": ["S"]}])
    option = main.match_ride(main.MatchRequest(
        passenger_name="Marie", origin=VEVEY, destination=BULLE, requested_time=f"{TOMORROW}T08:00",
    ))["transit_option"]
    assert option["fare"]["source"] == "t601"
    assert option["fare"]["half_chf"] == pytest.approx(option["fare"]["full_chf"] / 2, abs=0.01)


def test_drives_posted_before_prices_get_the_default(isolated_db, force_haversine):
    drive = _post()
    with db.get_conn() as conn:
        conn.execute("UPDATE drives SET driver_price_chf = NULL WHERE id = ?", (drive["id"],))
    listed = main.get_drives()[0]
    assert listed["driver_price_chf"] == _band()["default_chf"]


def test_init_db_adds_the_price_column_to_an_existing_database(isolated_db):
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("DROP TABLE drives")
        conn.execute("""
            CREATE TABLE drives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL, driver_name TEXT NOT NULL,
                origin_name TEXT NOT NULL, origin_lat REAL NOT NULL, origin_lon REAL NOT NULL,
                destination_name TEXT NOT NULL,
                destination_lat REAL NOT NULL, destination_lon REAL NOT NULL,
                departure_time TEXT NOT NULL, seats_available INTEGER NOT NULL DEFAULT 1,
                route_geometry TEXT, route_distance_m REAL, route_duration_s REAL,
                route_source TEXT, created_at TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            INSERT INTO drives (driver_id, driver_name, origin_name, origin_lat, origin_lon,
                destination_name, destination_lat, destination_lon, departure_time,
                route_distance_m, created_at)
            VALUES (1, 'Old Driver', 'A', 46.5, 6.6, 'B', 46.2, 6.1, '2026-11-01T08:00', 30000, 'x')
        """)

    db.init_db()
    db.init_db()  # and again, as at every start

    [old] = db.list_drives(upcoming_only=False, with_geometry=False)
    assert old["driver_price_chf"] is None
    assert pricing.with_prices(old)["price_chf"] == 6.30
