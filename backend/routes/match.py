"""
POST /api/match -- a passenger asks for a ride; the app finds the drives
whose route passes near their start and their destination, around their
time (search.py's fixed limits, see matching.py), the best first; when
none of them can be booked, also the bookable ones passing further away
("wider"). The best ones get public transport to the pickup and from the
drop-off where that makes sense (transit_legs.py), and a pure-public-
transport option is offered next to them (see transit.py). Matches say
whether they're "bookable": demo drives aren't.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import auth
import bookings
import database as db
import meeting_points
import pricing
import profiles
import ratings
import search
import transit
import transit_legs
from api import LatLon, api_error, current_session

router = APIRouter()

class MatchRequest(BaseModel):
    origin: LatLon
    destination: LatLon
    requested_time: str  # ISO 8601


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

    driver_matches = search.matches_for_search(passenger_origin, passenger_destination, payload.requested_time)

    # What the passenger pays for their part of the drive, given the seats
    # already booked, and the train fare for it (see pricing.py).
    bookings.with_seats_booked(driver_matches)
    ratings.with_driver_ratings(driver_matches)
    driver_matches = [pricing.with_ride_prices(m) for m in driver_matches]
    # The real drivers found are candidates for this ride: a signed-in
    # passenger may see their profiles (profiles.py).
    if user and auth.profile_complete(user) and not (user["is_demo"] or found["session"]["is_demo"]):
        profiles.allow_candidates(user["id"], [m["driver_id"] for m in driver_matches if not m["is_demo"]])

    # Public transport to the pickup and from the drop-off, where they're too
    # far to walk.
    transit_legs.add_to(driver_matches, payload.origin, payload.destination)

    # Names for the pickup and drop-off points, and the way on from the drop-off.
    meeting_points.add_to(driver_matches, payload.origin, payload.destination)

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
        "transit_option": transit_option,
        # For the page's sentence about drives found further away.
        "search_radius_km": search.SEARCH_RADIUS_KM,
        "wider_radius_km": search.WIDER_RADIUS_KM,
    }
