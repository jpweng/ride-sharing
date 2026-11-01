import pricing


def test_demo_price_rounds_up_to_50_centimes():
    assert pricing.demo_price_chf(70) == 10.5  # 70 km x 0.15 = 10.50
    assert pricing.demo_price_chf(71) == 11.0  # 10.65 -> 11.00


def test_demo_price_has_a_minimum():
    assert pricing.demo_price_chf(2) == pricing.DEMO_MIN_CHF


def test_unknown_distance_has_no_price():
    assert pricing.demo_price_chf(None) is None
    assert pricing.with_demo_price({"route_distance_m": None})["price_chf"] is None
    assert pricing.with_demo_price({"route_distance_m": 70_000})["price_chf"] == 10.5
