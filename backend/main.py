"""
FastAPI backend for the ride-sharing prototype.

Two core use cases:
  1. POST /api/drives      -- a driver proposes a drive.
  2. POST /api/match       -- a passenger asks for a ride; the app finds
                               the drives whose route passes within their
                               pickup and drop-off radii, around their time
                               (see matching.py), adds a last-mile transit
                               connection from the drop-off for the best
                               ones, and also offers a pure-public-transport
                               option (see transit.py).
  GET /api/drives          -- browse all upcoming posted drives
                               (?driver_name= narrows to one driver's own).
  GET /api/drives/{id}     -- a single drive with full route geometry.
  DELETE /api/drives/{id}  -- a driver cancels one of their drives.
  GET /api/ride_requests   -- a passenger's recent searches.
  POST /api/requests       -- a passenger asks for a ride nobody has posted;
                               a few drivers who made the trip before are
                               told (see notify.py).
  GET/DELETE /api/requests -- a passenger's open ride requests; cancel one.
  GET /api/notifications   -- a user's inbox; .../read, .../{id}/action and
                               /api/notification_settings act on it.

No authentication: a driver/passenger is just a name, auto-registered on
first use. This is a demo/prototype, not a production app.

An hourly background task keeps a rolling week of fictional demo drives in
the database and deletes visitors' data a month after it's last useful (see
maintenance.py).
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database as db
import maintenance
import matching
import notify
import pricing
import search
import transit

logger = logging.getLogger("uvicorn.error")

MAINTENANCE_INTERVAL_S = 3600


async def _maintenance_loop():
    # One loop per process; fine because the app runs as a single uvicorn
    # worker (and maintenance.run() is idempotent anyway).
    while True:
        try:
            changes = await asyncio.to_thread(maintenance.run)
            if any(changes.values()):
                logger.info("Maintenance: %s", changes)
        except Exception:
            logger.exception("Maintenance failed")
        await asyncio.sleep(MAINTENANCE_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_maintenance_loop())
    yield
    task.cancel()


app = FastAPI(title="Ride and Rail", lifespan=lifespan)

db.init_db()

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# Beyond this distance, a drive's drop-off point is considered "not the
# passenger's actual destination", and we look for a last-mile transit leg.
LAST_MILE_THRESHOLD_KM = 1.0

# Last-mile connections are only looked up for the best few matches: each
# costs a few calls to transport.opendata.ch, a free, rate-limited API.
MAX_LAST_MILE_LOOKUPS = 5


def api_error(status: int, code: str, message: str) -> HTTPException:
    # The frontend shows its translation of `code` (errors.<code> in
    # frontend/i18n/*.json); `message` is for API users and logs.
    return HTTPException(status, {"code": code, "message": message})


class LatLon(BaseModel):
    name: str
    lat: float
    lon: float
    # How the user specified this point: "place" (picked a town/municipality
    # by name -- transit then starts/ends at that town's train station),
    # "address" (a street address) or "point" (clicked on the map). The
    # latter two use the nearest stop.
    kind: Literal["place", "address", "point"] = "point"


class DriveCreate(BaseModel):
    driver_name: str = Field(min_length=1)
    origin: LatLon
    destination: LatLon
    departure_time: str  # ISO 8601
    seats_available: int = Field(default=1, ge=1, le=8)


class MatchRequest(BaseModel):
    passenger_name: str = Field(min_length=1)
    origin: LatLon
    destination: LatLon
    requested_time: str  # ISO 8601
    # How far from their start / destination the passenger will meet the
    # driver, and how far from requested_time the pickup may be.
    pickup_radius_km: float = Field(default=matching.DEFAULT_PICKUP_RADIUS_KM, ge=0.5, le=50)
    dropoff_radius_km: float = Field(default=matching.DEFAULT_DROPOFF_RADIUS_KM, ge=0.5, le=50)
    time_tolerance_min: int = Field(default=matching.DEFAULT_TIME_TOLERANCE_MIN, ge=0, le=360)


class RideRequestCreate(MatchRequest):
    pass


class NotificationAction(BaseModel):
    name: str = Field(min_length=1)
    action: Literal["offer", "decline", "mute", "view"]


class NotificationSettings(BaseModel):
    name: str = Field(min_length=1)
    notify_requests: bool | None = None
    resume: bool = False  # turn notifications back on after the back-off paused them


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/drives")
def post_drive(payload: DriveCreate):
    try:
        datetime.fromisoformat(payload.departure_time)
    except ValueError:
        raise api_error(400, "invalid_time", "departure_time must be ISO 8601, e.g. 2026-09-22T08:30:00")

    route = matching.route_with_fallback([
        (payload.origin.lat, payload.origin.lon),
        (payload.destination.lat, payload.destination.lon),
    ])

    drive_id = db.create_drive(
        driver_name=payload.driver_name.strip(),
        origin_name=payload.origin.name,
        origin_lat=payload.origin.lat,
        origin_lon=payload.origin.lon,
        destination_name=payload.destination.name,
        destination_lat=payload.destination.lat,
        destination_lon=payload.destination.lon,
        departure_time=payload.departure_time,
        seats_available=payload.seats_available,
        route_geometry=route["geometry"],
        route_distance_m=route["distance_m"],
        route_duration_s=route["duration_s"],
        route_source=route["source"],
    )
    drive = db.get_drive(drive_id)
    # Passengers waiting for a ride like this one are told.
    notify.tell_passengers(drive)
    return pricing.with_demo_price(drive)


@app.get("/api/drives")
def get_drives(
    upcoming_only: bool = True,
    driver_name: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
):
    # Without route geometry, which can be ~60 KB per drive: the map gets it
    # from /api/drives/{id} for the drive it shows.
    drives = db.list_drives(
        upcoming_only=upcoming_only, driver_name=driver_name, with_geometry=False, limit=limit
    )
    return [pricing.with_demo_price(d) for d in drives]


@app.get("/api/drives/{drive_id}")
def get_drive(drive_id: int):
    drive = db.get_drive(drive_id)
    if not drive:
        raise api_error(404, "drive_not_found", "Drive not found")
    return pricing.with_demo_price(drive)


@app.delete("/api/drives/{drive_id}")
def cancel_drive(drive_id: int, driver_name: str):
    # No real auth in this prototype -- requiring the driver's name just keeps
    # one user from cancelling someone else's drive by accident.
    drive = db.get_drive(drive_id)
    if not drive:
        raise api_error(404, "drive_not_found", "Drive not found")
    if drive["driver_name"] != driver_name.strip():
        raise api_error(403, "not_your_drive", "Only the driver who posted this drive can cancel it")
    db.delete_drive(drive_id)
    return {"deleted": drive_id}


@app.get("/api/ride_requests")
def get_ride_requests(passenger_name: str):
    return db.list_recent_ride_requests(passenger_name)


@app.post("/api/match")
def match_ride(payload: MatchRequest):
    try:
        datetime.fromisoformat(payload.requested_time)
    except ValueError:
        raise api_error(400, "invalid_time", "requested_time must be ISO 8601, e.g. 2026-09-22T08:30:00")

    db.save_ride_request(
        passenger_name=payload.passenger_name.strip(),
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

    driver_matches = search.upcoming_matches(
        passenger_origin, passenger_destination, payload.requested_time,
        pickup_radius_km=payload.pickup_radius_km,
        dropoff_radius_km=payload.dropoff_radius_km,
        time_tolerance_min=payload.time_tolerance_min,
    )

    # Their share of the ride: a demo price for now (see pricing.py).
    for m in driver_matches:
        m["price_chf"] = pricing.demo_price_chf(m["ride_km"])

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
        "transit_option": transit_option,
    }


@app.post("/api/requests")
def create_ride_request(payload: RideRequestCreate):
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
        payload.passenger_name.strip(), payload.origin.model_dump(), payload.destination.model_dump(),
        asked.isoformat(timespec="minutes"),
        payload.pickup_radius_km, payload.dropoff_radius_km, payload.time_tolerance_min,
    )
    told = notify.notify_drivers(request)
    return {"request": request, "drivers_told": len(told)}


@app.get("/api/requests")
def get_ride_requests_open(passenger_name: str):
    return db.list_open_requests(notify.local_now().isoformat(timespec="minutes"), passenger_name)


@app.delete("/api/requests/{request_id}")
def cancel_ride_request(request_id: int, passenger_name: str):
    request = db.get_open_request(request_id)
    if not request:
        raise api_error(404, "request_not_found", "Ride request not found")
    if request["passenger_name"] != passenger_name.strip():
        raise api_error(403, "not_your_request", "Only the passenger who made this request can cancel it")
    db.set_request_status(request_id, "cancelled")
    return {"cancelled": request_id}


@app.get("/api/notifications")
def get_notifications(name: str):
    return notify.inbox(name.strip())


@app.post("/api/notifications/read")
def read_notifications(name: str):
    return {"read": db.mark_notifications_read(name)}


@app.post("/api/notifications/{notification_id}/action")
def act_on_notification(notification_id: int, payload: NotificationAction):
    n = db.get_notification(notification_id)
    if not n or n["recipient_name"] != payload.name.strip():
        raise api_error(404, "notification_not_found", "Notification not found")
    if payload.action == "mute":
        request = db.get_open_request(n["request_id"])
        if request:
            db.add_route_mute(
                payload.name.strip(),
                (request["origin_lat"], request["origin_lon"]),
                (request["destination_lat"], request["destination_lon"]),
            )
    db.set_notification_action(notification_id, payload.action)
    return db.get_notification(notification_id)


@app.put("/api/notification_settings")
def update_notification_settings(payload: NotificationSettings):
    db.update_user_settings(payload.name.strip(), payload.notify_requests, reset_backoff=payload.resume)
    return notify.inbox(payload.name.strip())


# Serve the static frontend last, so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
