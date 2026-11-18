"""GET /api/price_band -- a trip's price band, for the driver's form before
posting (see pricing.py, the module these routes call)."""
from typing import Annotated

from fastapi import APIRouter, Query

import matching
import pricing
from api import api_error

router = APIRouter()


@router.get("/api/price_band")
def get_price_band(
    origin_lat: Annotated[float, Query(ge=-90, le=90)],
    origin_lon: Annotated[float, Query(ge=-180, le=180)],
    destination_lat: Annotated[float, Query(ge=-90, le=90)],
    destination_lon: Annotated[float, Query(ge=-180, le=180)],
):
    # The driver's form asks before posting: the same road route as
    # POST /api/drives computes, so the same band.
    route = matching.route_with_fallback([(origin_lat, origin_lon), (destination_lat, destination_lon)])
    km = route["distance_m"] / 1000.0
    price_band = pricing.band(km)
    if not price_band:
        raise api_error(400, "no_distance", "Start and destination are the same place")
    return {
        **price_band,
        # Each passenger's ceiling with 1, 2, ... 8 passengers: why the
        # price per passenger falls when more people join.
        "ceilings_chf": [pricing.ceiling_chf(km, n) for n in range(1, 9)],
    }
