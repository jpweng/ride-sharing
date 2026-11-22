"""
POST /api/match -- a passenger asks for a ride; the app finds the drives
whose route passes near their start and their destination, around their
time (search.py's fixed limits, see matching.py), the best first; when
none of them can be booked, also the bookable ones passing further away
("wider"). The best ones get public transport to the pickup and from the
drop-off where that makes sense (transit_legs.py), and a pure-public-
transport option is offered next to them (see transit.py). Matches say
whether they're "bookable": demo drives aren't.

Next to those, "combined_matches": a ride for part of the way with public
transport for the rest (combined.py).

The parts that wait for the timetable service run side by side: public
transport alone from the start, the names of the meeting points and the
legs to and from them while the combined trips are found.

Searching is public. Someone who isn't signed in gets the matches without
their drivers' names (api.without_driver): the rating shows, the name and
the profile come with signing in.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import auth
import bookings
import combined
import database as db
import meeting_points
import pricing
import profiles
import ratings
import search
import transit
import transit_legs
from api import LatLon, api_error, current_session, is_member, without_driver

router = APIRouter()

# The parts of a search that only wait for the timetable service, next to the
# request's own thread. None of them waits for another one.
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="search-parts")


class MatchRequest(BaseModel):
    origin: LatLon
    destination: LatLon
    requested_time: str  # ISO 8601
    # What the time is: when the passenger wants to leave, or when they want
    # to be there (see matching.py).
    time_kind: Literal["depart", "arrive"] = "depart"


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
            time_kind=payload.time_kind,
        )

    passenger_origin = (payload.origin.lat, payload.origin.lon)
    passenger_destination = (payload.destination.lat, payload.destination.lon)
    origin_place = payload.origin.name if payload.origin.kind == "place" else None
    destination_place = payload.destination.name if payload.destination.kind == "place" else None

    # Pure public-transport alternative, origin to final destination, for
    # comparison. Nothing else is needed for it: asked for straight away.
    transit_alone = _pool.submit(
        transit.transit_option_for_trip,
        payload.origin.lat, payload.origin.lon,
        payload.destination.lat, payload.destination.lon,
        payload.requested_time,
        origin_place=origin_place,
        destination_place=destination_place,
        arrive_by=payload.time_kind == "arrive",
    )

    driver_matches = search.matches_for_search(
        passenger_origin, passenger_destination, payload.requested_time, payload.time_kind,
    )

    # What the passenger pays for their part of the drive, given the seats
    # already booked, and the train fare for it (see pricing.py).
    bookings.with_seats_booked(driver_matches)
    ratings.with_driver_ratings(driver_matches)
    driver_matches = [pricing.with_ride_prices(m) for m in driver_matches]
    # The real drivers found are candidates for this ride: a signed-in
    # passenger may see their profiles (profiles.py), and their photos show
    # on the matches.
    viewer_id = None
    if user and auth.profile_complete(user) and not (user["is_demo"] or found["session"]["is_demo"]):
        profiles.allow_candidates(user["id"], [m["driver_id"] for m in driver_matches if not m["is_demo"]])
        viewer_id = user["id"]
    profiles.with_driver_photos(driver_matches, viewer_id)

    def with_the_way_to_them():
        # Public transport to the pickup and from the drop-off, where they're
        # too far to walk.
        transit_legs.add_to(driver_matches, payload.origin, payload.destination)
        # Names for the pickup and drop-off points, and the way on from the drop-off.
        meeting_points.add_to(driver_matches, payload.origin, payload.destination)

    the_way_to_them = _pool.submit(with_the_way_to_them)

    # Meanwhile: a ride for part of the way and public transport for the rest
    # (combined.py), with the drives that don't pass near both ends. It
    # compares them with public transport alone once it has found them.
    combined_matches = combined.find(
        payload.origin, payload.destination, payload.requested_time, payload.time_kind,
        exclude_ids={m["id"] for m in driver_matches}, transit_option=transit_alone.result,
    )
    the_way_to_them.result()
    transit_option = transit_alone.result()
    bookings.with_seats_booked(combined_matches)
    ratings.with_driver_ratings(combined_matches)
    combined_matches = [search.with_bookable(pricing.with_ride_prices(m)) for m in combined_matches]
    if viewer_id is not None:
        profiles.allow_candidates(viewer_id, [m["driver_id"] for m in combined_matches if not m["is_demo"]])
    profiles.with_driver_photos(combined_matches, viewer_id)

    if not is_member(found):
        driver_matches = [without_driver(m) for m in driver_matches]
        combined_matches = [without_driver(m) for m in combined_matches]

    return {
        "driver_matches": driver_matches,
        "combined_matches": combined_matches,
        "transit_option": transit_option,
        # For the page's sentence about drives found further away.
        "search_radius_km": search.SEARCH_RADIUS_KM,
        "wider_radius_km": search.WIDER_RADIUS_KM,
    }
