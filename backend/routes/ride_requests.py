"""
A passenger's searches and ride requests:

  GET /api/ride_requests   -- the signed-in passenger's recent searches.
  POST /api/requests       -- a passenger asks for a ride nobody has posted;
                              a few drivers who made the trip before are
                              told (see notify.py).
  GET/DELETE /api/requests -- a passenger's open ride requests; cancel one.
  POST /api/requests/{id}/accept -- a driver takes a passenger's request: the
                              drive is posted and the passenger booked on it
                              (see bookings.accept_request).
"""
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

import bookings
import database as db
import drive_conflicts
import matching
import notify
import search
from api import LatLon, Member, Writer, api_error
from routes.match import MatchRequest

router = APIRouter()


class RideRequestCreate(MatchRequest):
    pass


@router.get("/api/ride_requests")
def get_ride_requests(user: Member):
    return db.list_recent_ride_requests(user["name"])


@router.post("/api/requests")
def create_ride_request(payload: RideRequestCreate, user: Writer):
    try:
        asked = datetime.fromisoformat(payload.requested_time).replace(tzinfo=None)
    except ValueError:
        raise api_error(400, "invalid_time", "requested_time must be ISO 8601, e.g. 2026-09-22T08:30:00")
    # No trip to drive: drivers with an area there would be told all the same.
    if matching.haversine_km(payload.origin.lat, payload.origin.lon,
                             payload.destination.lat, payload.destination.lon) <= 0:
        raise api_error(400, "no_distance", "Start and destination are the same place")
    now = notify.local_now()
    if asked < now + notify.REQUEST_MIN_AHEAD:
        raise api_error(400, "request_too_soon", "A ride request must be at least 15 minutes ahead")
    if asked > now + notify.REQUEST_MAX_AHEAD:
        raise api_error(400, "request_too_far", "A ride request can be at most 14 days ahead")
    # To arrive by a time: when the passenger's own trip has to start for
    # that. A driver who takes the request as it is leaves then, and drivers
    # need the same notice as for a time to leave at.
    leave_by = None
    if payload.time_kind == "arrive":
        leave_by = notify.leave_by(payload.origin, payload.destination, asked)
        if leave_by < now + notify.REQUEST_MIN_AHEAD:
            raise api_error(400, "arrival_too_soon", "The drive there takes too long to arrive by then")

    request = db.create_open_request(
        user["name"], payload.origin.model_dump(), payload.destination.model_dump(),
        asked.isoformat(timespec="minutes"),
        # As a search: the limits aren't the passenger's to choose.
        search.SEARCH_RADIUS_KM, search.SEARCH_RADIUS_KM, search.SEARCH_TIME_TOLERANCE_MIN,
        time_kind=payload.time_kind,
        leave_by=leave_by.isoformat(timespec="minutes") if leave_by else None,
    )
    told = notify.notify_drivers(request)
    # A real passenger's request only reaches real drivers, so the count is
    # of real drivers. A demo request (made under a demo driver's name) only
    # reaches demo drivers, and gets their (fictional) names, so the demo
    # can go on with signing in as one of them.
    result = {"request": request, "drivers_told": len(told), "demo_request": notify.is_demo_request(request)}
    if result["demo_request"]:
        result["demo_drivers_told"] = told
    return result


@router.get("/api/requests")
def get_ride_requests_open(user: Member):
    return db.list_open_requests(notify.local_now().isoformat(timespec="minutes"), user["name"])


@router.delete("/api/requests/{request_id}")
def cancel_ride_request(request_id: int, user: Writer):
    request = db.get_open_request(request_id)
    if not request:
        raise api_error(404, "request_not_found", "Ride request not found")
    if request["passenger_id"] != user["id"]:
        raise api_error(403, "not_your_request", "Only the passenger who made this request can cancel it")
    db.set_request_status(request_id, "cancelled")
    return {"cancelled": request_id}


class RequestAccept(BaseModel):
    # The seats the driver offers in all, the passenger's included.
    seats_available: int = Field(default=bookings.DEFAULT_SEATS_OFFERED, ge=1, le=8)
    note: str | None = Field(default=None, max_length=bookings.MAX_NOTE_CHARS)
    price_chf: float | None = Field(default=None, ge=0, le=1000)
    # The driver's own trip (their usual one, from the notification); all
    # three or none: without them, the passenger's trip at their time.
    origin: LatLon | None = None
    destination: LatLon | None = None
    departure_time: str | None = None  # ISO 8601

    @model_validator(mode="after")
    def _a_whole_trip_or_none(self):
        given = [self.origin is not None, self.destination is not None, self.departure_time is not None]
        if any(given) and not all(given):
            raise ValueError("give origin, destination and departure_time together, or none of them")
        return self


@router.post("/api/requests/{request_id}/accept")
def accept_ride_request(request_id: int, payload: RequestAccept, user: Writer):
    request = db.get_open_request(request_id)
    if not request:
        raise api_error(404, "request_not_found", "Ride request not found")
    trip = None
    if payload.origin:
        try:
            datetime.fromisoformat(payload.departure_time)
        except ValueError:
            raise api_error(400, "invalid_time", "departure_time must be ISO 8601, e.g. 2026-09-22T08:30:00")
        trip = {
            "origin": payload.origin.model_dump(), "destination": payload.destination.model_dump(),
            "departure_time": payload.departure_time,
        }
    # It posts a drive: one of a driver's at a time (drive_conflicts.py).
    with drive_conflicts.one_at_a_time(user["name"]):
        return bookings.accept_request(request, user, payload.seats_available, payload.note, trip, payload.price_chf)
