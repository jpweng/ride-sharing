"""
A passenger's searches and ride requests:

  GET /api/ride_requests   -- the signed-in passenger's recent searches.
  POST /api/requests       -- a passenger asks for a ride nobody has posted;
                              a few drivers who made the trip before are
                              told (see notify.py).
  GET/DELETE /api/requests -- a passenger's open ride requests; cancel one.
"""
from datetime import datetime

from fastapi import APIRouter

import database as db
import notify
from api import Member, Writer, api_error
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
    now = notify.local_now()
    if asked < now + notify.REQUEST_MIN_AHEAD:
        raise api_error(400, "request_too_soon", "A ride request must be at least 1 hour ahead")
    if asked > now + notify.REQUEST_MAX_AHEAD:
        raise api_error(400, "request_too_far", "A ride request can be at most 14 days ahead")

    request = db.create_open_request(
        user["name"], payload.origin.model_dump(), payload.destination.model_dump(),
        asked.isoformat(timespec="minutes"),
        payload.pickup_radius_km, payload.dropoff_radius_km, payload.time_tolerance_min,
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
