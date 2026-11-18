"""
Tests for pricing.py: the band a driver's price must stay in (train fare,
shared-cost ceiling, fuel minimum, default suggestion) and what each
passenger pays as more people join.
"""
import pytest

import pricing

TRAIN_30_KM = {"full_chf": 12.60, "half_chf": 6.30, "approximate": True, "source": "t601", "tariff_km": 30.0}


def test_worked_example_30_km_one_passenger():
    band = pricing.band(30)
    # Train: the T601 table's 29-30 km zone, 2nd class.
    assert band["train_fare"]["full_chf"] == 12.60
    assert band["train_fare"]["half_chf"] == 6.30
    # Legal ceiling: the car's real cost (0.75 x 30 = 22.50) shared by driver and passenger.
    assert band["ceiling_chf"] == 11.25
    assert band["fuel_chf"] == 4.50
    assert band["min_chf"] == 4.50
    assert band["max_chf"] == 11.20  # 11.25 rounded down to 10 centimes
    assert band["default_chf"] == 6.30  # the half-fare price
    assert band["warn_above_chf"] == 6.30


def test_worked_example_with_a_train_fare_of_13_francs():
    # The figures of the original brief, with its estimated train fare.
    band = pricing.band(30, {**TRAIN_30_KM, "full_chf": 13.00, "half_chf": 6.50})
    assert (band["min_chf"], band["max_chf"], band["default_chf"], band["warn_above_chf"]) == (4.50, 11.20, 6.50, 6.50)


def test_ceiling_falls_with_more_passengers():
    # 22.50 shared by 2, 3, 4, 5 people; 5.625 rounds *down* to 5 centimes.
    assert [pricing.ceiling_chf(30, n) for n in (1, 2, 3, 4)] == [11.25, 7.50, 5.60, 4.50]


def test_each_passenger_pays_less_when_more_join():
    prices = [pricing.passenger_price_chf(11.20, 30, n) for n in (1, 2, 3, 4)]
    assert prices == [11.20, 7.50, 5.60, 4.50]
    # A driver asking less than the ceiling keeps their price until the ceiling is lower.
    assert [pricing.passenger_price_chf(6.30, 30, n) for n in (1, 2, 3, 4)] == [6.30, 6.30, 5.60, 4.50]


@pytest.mark.parametrize("km", [0.5, 3, 12.3, 30, 77.7, 150, 420])
@pytest.mark.parametrize("passengers", range(1, 9))
def test_driver_never_collects_more_than_the_cost_minus_their_share(km, passengers):
    band = pricing.band(km)
    collected = passengers * pricing.passenger_price_chf(band["max_chf"], km, passengers)
    assert collected <= pricing.CAR_COST_CHF_PER_KM * km * passengers / (passengers + 1) + 1e-9


@pytest.mark.parametrize("km", [0.5, 3, 12.3, 30, 77.7, 150, 420])
def test_band_is_consistent(km):
    band = pricing.band(km)
    assert 0 < band["min_chf"] <= band["default_chf"] <= band["max_chf"]
    assert band["max_chf"] <= band["ceiling_chf"]
    assert band["max_chf"] <= band["train_fare"]["full_chf"]


def test_long_trips_are_capped_at_the_train():
    band = pricing.band(300)
    assert band["ceiling_chf"] == 112.50
    assert band["max_chf"] == band["train_fare"]["full_chf"] == 84.00


def test_minimum_never_above_the_maximum_on_very_short_trips():
    band = pricing.band(2)
    assert band["max_chf"] == 0.70  # ceiling 0.75 x 2 / 2
    assert band["min_chf"] == 0.30  # fuel
    tiny = pricing.band(0.5)
    assert tiny["min_chf"] == tiny["max_chf"] == 0.10


def test_unknown_distance_has_no_band():
    assert pricing.band(None) is None
    assert pricing.band(0) is None


def test_check_driver_price():
    band = pricing.band(30)
    assert pricing.check_driver_price(11.20, band) is None
    assert pricing.check_driver_price(4.50, band) is None
    assert pricing.check_driver_price(11.30, band) == "price_too_high"
    assert pricing.check_driver_price(4.40, band) == "price_too_low"


def _drive(km, driver_price=None, seats=3):
    return {"route_distance_m": km * 1000, "driver_price_chf": driver_price, "seats_available": seats}


def test_drive_prices_for_one_and_more_passengers():
    priced = pricing.with_prices(_drive(30, 11.20, seats=4))
    assert priced["price_chf"] == 11.20
    assert priced["price_by_passengers"] == [11.20, 7.50, 5.60, 4.50]
    assert priced["train_fare"]["full_chf"] == 12.60
    assert priced["driver_price_chf"] == 11.20


def test_drive_without_a_price_gets_the_default():
    priced = pricing.with_prices(_drive(30))
    assert priced["driver_price_chf"] == priced["price_chf"] == 6.30


def test_drive_with_unknown_distance_has_no_price():
    priced = pricing.with_prices({"route_distance_m": None, "driver_price_chf": None, "seats_available": 3})
    assert priced["price_chf"] is None
    assert priced["train_fare"] is None


def test_part_of_a_drive_costs_its_share_of_the_drivers_price():
    # 100 km drive at CHF 20; the passenger rides 30 km of it: 6.00.
    match = {**_drive(100, 20.00), "ride_km": 30.0}
    priced = pricing.with_ride_prices(match)
    assert priced["price_chf"] == 6.00
    assert priced["train_fare"]["full_chf"] == 12.60
    # At the drive's maximum, the part is capped by its own ceiling and train fare.
    priced = pricing.with_ride_prices({**_drive(100, 34.00), "ride_km": 30.0})
    assert priced["price_chf"] == 10.20  # 34 x 0.3, under the 11.25 ceiling
    priced = pricing.with_ride_prices({**_drive(100, 34.00, seats=2), "ride_km": 30.0})
    assert priced["price_by_passengers"] == [10.20, 7.50]
