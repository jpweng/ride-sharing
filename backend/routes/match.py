"""
POST /api/match -- a passenger asks for a ride; the app finds the drives
whose route passes within their pickup and drop-off radii, around their
time (see matching.py) and a few just outside them, adds a last-mile
transit connection from the drop-off for the best ones, and also offers a
pure-public-transport option (see transit.py). Matches say whether they're
"bookable": demo drives aren't.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import auth
import bookings
import database as db
import matching
import pricing
import ratings
import search
import transit
from api import LatLon, api_error, current_session

router = APIRouter()

# Beyond this distance, a drive's drop-off point is considered "not the
# passenger's actual destination", and we look for a last-mile transit leg.
LAST_MILE_THRESHOLD_KM = 1.0

# Last-mile connections are only looked up for the best few matches: each
# costs a few calls to transport.opendata.ch, a free, rate-limited API.
MAX_LAST_MILE_LOOKUPS = 5


class MatchRequest(BaseModel):
    origin: LatLon
    destination: LatLon
    requested_time: str  # ISO 8601
    # How far from their start / destination the passenger will meet the
    # driver, and how far from requested_time the pickup may be.
    pickup_radius_km: float = Field(default=matching.DEFAULT_PICKUP_RADIUS_KM, ge=0.5, le=50)
    dropoff_radius_km: float = Field(default=matching.DEFAULT_DROPOFF_RADIUS_KM, ge=0.5, le=50)
    time_tolerance_min: int = Field(default=matching.DEFAULT_TIME_TOLERANCE_MIN, ge=0, le=360)


@router.post("/api/match")
def match_ride(payload: MatchRequest, found: Annotated[dict | None, Depends(current_session)] = None):
    try:
        datetime.fromisoformat(payload.requested_time)
    except ValueError:
        raise api_error(400, "invalid_time", "requested_time must be ISO 8601, e.g. 2026-09-22T08:30:00")

    # Searching is public; a signed-in passenger's search is kept for their
    # "recent searches" (and ride predictions later). Not demo sessions'.
    user = found["user"] if found else None
    if user and auth.profile_complete(user) and not (user["is_demo"] or found["session"]["is_demo"]):
        db.save_ride_request(
            passenger_name=user["name"],
            origin_name=payload.origin.name,
            origin_lat=payload.origin.lat,
            origin_lon=payload.origin.lon,
            destination_name=payload.destination.name,
            destination_lat=payload.destination.lat,
            destination_lon=payload.destination.lon,
            requested_time=payload.requested_time,
        )

    passenger_origin = (payload.origin.lat, payload.origin.lon)
    passenger_destination = (payload.destination.lat, payload.destination.lon)
    origin_place = payload.origin.name if payload.origin.kind == "place" else None
    destination_place = payload.destination.name if payload.destination.kind == "place" else None

    driver_matches, outside_matches = search.with_outside(
        passenger_origin, passenger_destination, payload.requested_time,
        pickup_radius_km=payload.pickup_radius_km,
        dropoff_radius_km=payload.dropoff_radius_km,
        time_tolerance_min=payload.time_tolerance_min,
    )

    # What the passenger pays for their part of the drive, given the seats
    # already booked, and the train fare for it (see pricing.py).
    bookings.with_seats_booked(driver_matches + outside_matches)
    ratings.with_driver_ratings(driver_matches + outside_matches)
    driver_matches = [pricing.with_ride_prices(m) for m in driver_matches]
    outside_matches = [pricing.with_ride_prices(m) for m in outside_matches]

    # A public transport connection from the drop-off to the passenger's
    # actual destination, when it's too far to walk.
    destination_station = None
    for i, m in enumerate(driver_matches):
        m["last_mile_transit"] = None
        if i >= MAX_LAST_MILE_LOOKUPS or m["dropoff"]["gap_km"] <= LAST_MILE_THRESHOLD_KM:
            continue
        if destination_station is None:
            destination_station = transit.resolve_station(
                payload.destination.lat, payload.destination.lon, destination_place
            ) or {}
        from_station = transit.nearest_named_station(m["dropoff"]["lat"], m["dropoff"]["lon"])
        if not from_station or not destination_station or from_station["name"] == destination_station["name"]:
            continue
        connections = transit.get_connections(from_station["name"], destination_station["name"], m["dropoff"]["time"])
        if connections:
            m["last_mile_transit"] = {
                "from_station": from_station["name"],
                "to_station": destination_station["name"],
                "connections": connections,
            }

    # Pure public-transport alternative, origin to final destination, for comparison.
    transit_option = transit.transit_option_for_trip(
        payload.origin.lat, payload.origin.lon,
        payload.destination.lat, payload.destination.lon,
        payload.requested_time,
        origin_place=origin_place,
        destination_place=destination_place,
    )

    return {
        "driver_matches": driver_matches,
        # Just outside the passenger's circles or time flexibility, marked
        # as such: they may like them anyway. No last-mile lookups.
        "outside_matches": outside_matches,
        "transit_option": transit_option,
    }
