"""
Demo prices, shown on rides so the demo looks like the real thing.

A placeholder: pricing is still to be designed. The numbers are a rough
cost share (fuel and wear split between driver and passenger), not a
product decision, and nothing is charged.
"""
import math

DEMO_CHF_PER_KM = 0.15
DEMO_MIN_CHF = 3.0
DEMO_ROUND_TO_CHF = 0.5


def demo_price_chf(km: float | None) -> float | None:
    """Price of riding `km` km, rounded up to 50 centimes; None if unknown."""
    if km is None:
        return None
    price = math.ceil(km * DEMO_CHF_PER_KM / DEMO_ROUND_TO_CHF) * DEMO_ROUND_TO_CHF
    return max(DEMO_MIN_CHF, price)


def with_demo_price(drive: dict) -> dict:
    """A drive with the demo price of the whole trip, as price_chf."""
    km = drive["route_distance_m"] / 1000.0 if drive.get("route_distance_m") else None
    return {**drive, "price_chf": demo_price_chf(km)}
