"""
Ride prices: the band a driver's price must stay in, and what each
passenger pays.

In Switzerland, carpooling is legal as long as the driver only shares the
costs of the trip. A driver who regularly earns more than the trip really
costs is doing commercial passenger transport, which needs a professional
licence. Hence these rules (nothing is charged yet: the app shows prices,
passengers and drivers settle between themselves):

1. Capped at the train. A passenger never pays more than the train's 2nd
   class full fare for the same trip (fares.py).

2. Cost shared, legal ceiling. The car really costs CAR_COST_CHF_PER_KM
   (CHF 0.75/km, a TCS-based benchmark: fuel, wear, insurance, depreciation).
   That cost is split among everyone in the car, driver included, so with
   n passengers each pays at most

       ceiling(n) = CAR_COST_CHF_PER_KM x km / (n + 1)

   and the driver collects at most n x ceiling(n) = cost x n / (n + 1): the
   real cost minus their own share, whatever the number of passengers.

3. The driver sets the price. When posting a drive, the driver chooses one
   price per passenger, for one passenger, for the whole drive, inside the
   band [minimum, min(ceiling(1), train full fare)]. The API rejects a price
   outside it. The UI warns when it's above WARN_ABOVE_SHARE_OF_FULL_FARE of
   the train's full fare (the half-fare ticket price).

   How many passengers there will be is only known when seats are booked,
   so the driver's price isn't what everyone pays. Each passenger pays

       min(driver's price, ceiling(current number of passengers), train full fare)

   so the price per passenger falls by itself when more people join: on a
   30 km drive at the maximum, CHF 11.20 alone, 7.50 each for two, 5.60
   each for three, 4.50 each for four. Seats are booked through
   bookings.py: price_chf is what the next passenger pays, given the seats
   already booked, and price_by_passengers lists the price for 1, 2, ...
   passengers up to a full car. n counts every passenger on the drive, even
   those who ride only part of it, which keeps the driver's total on the
   safe side.

4. Minimum price, growing with distance, around the driver's fuel:
   FUEL_CHF_PER_KM x km. Never above the maximum: on very short trips the
   ceiling or the train fare win.

5. Default suggestion: the half-fare ticket price
   (DEFAULT_SHARE_OF_FULL_FARE of the full fare), clamped to the band. Also
   used for drives without a price of their own (posted before prices
   existed) and for the demo drives.

A passenger riding only part of a drive pays the driver's price in
proportion to the distance they ride (ride_km / drive km), and the ceiling
and train fare of their own part.

Rounding: amounts are rounded *down* so that no cap is ever exceeded.
Ceilings to 5 centimes (CEILING_STEP_CHF: 22.50 / 4 = 5.625 -> 5.60), every
price shown to users, and the band's limits, to 10 centimes
(PRICE_STEP_CHF). The minimum is rounded up to 10 centimes (it's a floor).

Worked example, 30 km, one passenger: train CHF 12.60 full (T601 table,
29-30 km), half fare 6.30; ceiling 0.75 x 30 / 2 = 11.25; fuel 4.50. Band:
CHF 4.50 to 11.20, default 6.30, warning above 6.30.
"""
import math

import fares

# A car's real cost per km (TCS-based benchmark).
CAR_COST_CHF_PER_KM = 0.75
# The driver's fuel per km: the minimum price.
FUEL_CHF_PER_KM = 0.15
# Warn the driver above this share of the train's full fare: the half-fare
# ticket price. TO BE CONFIRMED.
WARN_ABOVE_SHARE_OF_FULL_FARE = 0.5
# The default price suggested to drivers: the half-fare ticket price.
DEFAULT_SHARE_OF_FULL_FARE = 0.5

CEILING_STEP_CHF = 0.05
PRICE_STEP_CHF = 0.10


def _round_down(chf: float, step: float) -> float:
    # The epsilon keeps exact multiples (7.50) from slipping a step down.
    return round(math.floor(chf / step + 1e-9) * step, 2)


def _round_up(chf: float, step: float) -> float:
    return round(math.ceil(chf / step - 1e-9) * step, 2)


def ceiling_chf(km: float, passengers: int = 1) -> float:
    """The most each of `passengers` may pay to share the car's cost over
    `km`: CAR_COST_CHF_PER_KM x km / (passengers + 1), rounded down to 5 centimes."""
    return _round_down(CAR_COST_CHF_PER_KM * km / (max(1, passengers) + 1), CEILING_STEP_CHF)


def band(km: float | None, train: dict | None = None) -> dict | None:
    """
    The price band for a driver's price (one passenger, `km` km): min_chf,
    max_chf, default_chf, warn_above_chf, and what they come from. `train`
    is the train fare (fares.py); by default the distance tariff for `km`.
    None if the distance is unknown.
    """
    if km is None or km <= 0:
        return None
    train = train or fares.fare_for_road_km(km)
    ceiling = ceiling_chf(km, 1)
    max_chf = _round_down(min(ceiling, train["full_chf"]), PRICE_STEP_CHF)
    min_chf = min(_round_up(FUEL_CHF_PER_KM * km, PRICE_STEP_CHF), max_chf)
    default = _round_down(DEFAULT_SHARE_OF_FULL_FARE * train["full_chf"], PRICE_STEP_CHF)
    return {
        "km": round(km, 1),
        "min_chf": min_chf,
        "max_chf": max_chf,
        "default_chf": min(max(default, min_chf), max_chf),
        "warn_above_chf": round(WARN_ABOVE_SHARE_OF_FULL_FARE * train["full_chf"], 2),
        "ceiling_chf": ceiling,
        "fuel_chf": round(FUEL_CHF_PER_KM * km, 2),
        "train_fare": train,
    }


def check_driver_price(price_chf: float, price_band: dict) -> str | None:
    """None if the driver's price is inside the band, else why not:
    "price_too_high" or "price_too_low"."""
    if price_chf > price_band["max_chf"] + 1e-9:
        return "price_too_high"
    if price_chf < price_band["min_chf"] - 1e-9:
        return "price_too_low"
    return None


def passenger_price_chf(driver_price_chf: float, km: float, passengers: int = 1, train_full_chf: float | None = None) -> float:
    """What each passenger pays over `km` with `passengers` in the car:
    min(driver's price, ceiling, train full fare), rounded down to 10 centimes."""
    if train_full_chf is None:
        train_full_chf = fares.fare_for_road_km(km)["full_chf"]
    price = min(driver_price_chf, ceiling_chf(km, passengers), train_full_chf)
    return _round_down(price, PRICE_STEP_CHF)


def _driver_price(drive: dict, drive_km: float) -> float:
    """The driver's price, or the default for drives without one."""
    if drive.get("driver_price_chf") is not None:
        return drive["driver_price_chf"]
    return band(drive_km)["default_chf"]


def _seats(drive: dict) -> tuple[int, int]:
    """(free seats, seats already booked): seats_booked is added to drives
    by bookings.with_seats_booked(); without it, nobody has booked."""
    free = drive.get("seats_available")
    return (1 if free is None else free), drive.get("seats_booked") or 0


def _prices(driver_price: float, km: float, seats: int, booked: int = 0) -> dict:
    """With `seats` free seats and `booked` passengers already aboard: each
    passenger's price with 1, 2, ... passengers up to a full car, and what
    the next passenger pays (in a full car: what everyone pays)."""
    train = fares.fare_for_road_km(km)
    by_passengers = [
        passenger_price_chf(driver_price, km, n, train["full_chf"]) for n in range(1, max(1, booked + seats) + 1)
    ]
    return {
        "price_chf": by_passengers[min(booked, len(by_passengers) - 1)],
        "price_by_passengers": by_passengers,
        "train_fare": train,
    }


def booking_price_chf(drive: dict, ride_km: float, passengers: int) -> float | None:
    """What each passenger pays per seat for `ride_km` of `drive` with
    `passengers` in the car (bookings.py): the driver's price in proportion
    to the distance ridden, capped like every ride price. None if the
    drive's distance is unknown."""
    drive_km = drive["route_distance_m"] / 1000.0 if drive.get("route_distance_m") else None
    if not drive_km or not ride_km or ride_km <= 0:
        return None
    share = _driver_price(drive, drive_km) * min(1.0, ride_km / drive_km)
    return passenger_price_chf(share, ride_km, max(1, passengers))


def _no_price() -> dict:
    return {"price_chf": None, "price_by_passengers": [], "train_fare": None}


def with_prices(drive: dict) -> dict:
    """
    A drive with what a passenger pays for the whole drive: price_chf (for
    the next passenger, given the seats already booked), price_by_passengers
    (each passenger's price with 1, 2, ... passengers, up to a full car),
    train_fare, and driver_price_chf (the driver's price, or the default
    when they set none).
    """
    km = drive["route_distance_m"] / 1000.0 if drive.get("route_distance_m") else None
    if not km:
        return {**drive, **_no_price()}
    driver_price = _driver_price(drive, km)
    return {**drive, "driver_price_chf": driver_price, **_prices(driver_price, km, *_seats(drive))}


def with_ride_prices(match: dict) -> dict:
    """
    A match (matching.py) with what the passenger pays for their part of the
    drive (ride_km): the driver's price in proportion to the distance,
    capped by the ceiling and the train fare of that part. Same fields as
    with_prices().
    """
    drive_km = match["route_distance_m"] / 1000.0 if match.get("route_distance_m") else None
    ride_km = match.get("ride_km")
    if not drive_km or not ride_km or ride_km <= 0:
        return {**match, **_no_price()}
    driver_price = _driver_price(match, drive_km)
    share = driver_price * min(1.0, ride_km / drive_km)
    return {**match, "driver_price_chf": driver_price, **_prices(share, ride_km, *_seats(match))}
