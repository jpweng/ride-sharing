"""
FastAPI backend for the ride-sharing prototype.

Two core use cases:
  1. POST /api/drives      -- a driver proposes a drive.
  2. POST /api/match       -- a passenger asks for a ride; the app ranks
                               posted drives by detour cost (see matching.py)
                               and also offers a pure-public-transport
                               option and, for matches that don't reach the
                               passenger's exact destination, a "drive +
                               last-mile transit" combo (see transit.py).
  GET /api/drives          -- browse all upcoming posted drives.
  GET /api/drives/{id}     -- a single drive with full route geometry.

No authentication: a driver/passenger is just a name, auto-registered on
first use. This is a demo/prototype, not a production app.
"""
import os
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database as db
import matching
import transit

app = FastAPI(title="Swiss Ride Share Prototype")

db.init_db()

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# Beyond this distance, a drive's drop-off point is considered "not the
# passenger's actual destination", and we look for a last-mile transit leg.
LAST_MILE_THRESHOLD_KM = 1.0


class LatLon(BaseModel):
    name: str
    lat: float
    lon: float


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


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.post("/api/drives")
def post_drive(payload: DriveCreate):
    try:
        datetime.fromisoformat(payload.departure_time)
    except ValueError:
        raise HTTPException(400, "departure_time must be ISO 8601, e.g. 2026-09-22T08:30:00")

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
    return db.get_drive(drive_id)


@app.get("/api/drives")
def get_drives(upcoming_only: bool = True):
    return db.list_drives(upcoming_only=upcoming_only)


@app.get("/api/drives/{drive_id}")
def get_drive(drive_id: int):
    drive = db.get_drive(drive_id)
    if not drive:
        raise HTTPException(404, "Drive not found")
    return drive


@app.post("/api/match")
def match_ride(payload: MatchRequest):
    try:
        datetime.fromisoformat(payload.requested_time)
    except ValueError:
        raise HTTPException(400, "requested_time must be ISO 8601, e.g. 2026-09-22T08:30:00")

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

    all_drives = db.list_drives(upcoming_only=True)
    passenger_origin = (payload.origin.lat, payload.origin.lon)
    passenger_destination = (payload.destination.lat, payload.destination.lon)

    driver_matches = matching.find_matches(
        all_drives, passenger_origin, passenger_destination, payload.requested_time
    )

    # For each match, if the driver's drop-off is meaningfully far from the
    # passenger's actual destination, suggest a last-mile public transport leg.
    for m in driver_matches:
        dropoff_gap_km = matching.haversine_km(
            m["destination_lat"], m["destination_lon"],
            payload.destination.lat, payload.destination.lon,
        )
        m["dropoff_gap_km"] = round(dropoff_gap_km, 2)
        if dropoff_gap_km > LAST_MILE_THRESHOLD_KM:
            m["last_mile_transit"] = transit.transit_option_for_trip(
                m["destination_lat"], m["destination_lon"],
                payload.destination.lat, payload.destination.lon,
                payload.requested_time,
            )
        else:
            m["last_mile_transit"] = None

    # Pure public-transport alternative, origin to final destination, for comparison.
    transit_option = transit.transit_option_for_trip(
        payload.origin.lat, payload.origin.lon,
        payload.destination.lat, payload.destination.lon,
        payload.requested_time,
    )

    return {
        "driver_matches": driver_matches,
        "transit_option": transit_option,
    }


# Serve the static frontend last, so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
