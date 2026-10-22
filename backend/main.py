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
  GET /api/drives          -- browse all upcoming posted drives
                               (?driver_name= narrows to one driver's own).
  GET /api/drives/{id}     -- a single drive with full route geometry.
  DELETE /api/drives/{id}  -- a driver cancels one of their drives.
  GET /api/ride_requests   -- a passenger's recent searches.

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
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database as db
import dropoff
import maintenance
import matching
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


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


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
def get_drives(upcoming_only: bool = True, driver_name: str | None = None):
    return db.list_drives(upcoming_only=upcoming_only, driver_name=driver_name)


@app.get("/api/drives/{drive_id}")
def get_drive(drive_id: int):
    drive = db.get_drive(drive_id)
    if not drive:
        raise HTTPException(404, "Drive not found")
    return drive


@app.delete("/api/drives/{drive_id}")
def cancel_drive(drive_id: int, driver_name: str):
    # No real auth in this prototype -- requiring the driver's name just keeps
    # one user from cancelling someone else's drive by accident.
    drive = db.get_drive(drive_id)
    if not drive:
        raise HTTPException(404, "Drive not found")
    if drive["driver_name"] != driver_name.strip():
        raise HTTPException(403, "Only the driver who posted this drive can cancel it")
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
    origin_place = payload.origin.name if payload.origin.kind == "place" else None
    destination_place = payload.destination.name if payload.destination.kind == "place" else None

    driver_matches = matching.find_matches(
        all_drives, passenger_origin, passenger_destination, payload.requested_time
    )

    # For each match, if the driver's own drop-off is meaningfully far from
    # the passenger's actual destination, see if the driver could instead let
    # them off somewhere closer -- but still on their own route -- with a
    # real transit connection covering the rest (see dropoff.py). This is
    # strictly an improvement over the driver's own destination: it's only
    # used when it has a smaller detour *and* an onward connection, so it
    # never makes a match worse, only refines where the passenger actually
    # gets dropped.
    for m in driver_matches:
        dropoff_gap_km = matching.haversine_km(
            m["destination_lat"], m["destination_lon"],
            payload.destination.lat, payload.destination.lon,
        )
        m["dropoff_gap_km"] = round(dropoff_gap_km, 2)
        m["recommended_dropoff"] = None
        if dropoff_gap_km <= LAST_MILE_THRESHOLD_KM:
            m["last_mile_transit"] = None
            continue

        best = dropoff.find_best_dropoff(
            m, passenger_origin, passenger_destination, payload.requested_time,
            destination_place=destination_place,
        )
        if best is not None:
            m["recommended_dropoff"] = {
                "station_name": best["station_name"],
                "lat": best["lat"],
                "lon": best["lon"],
            }
            m["detour_km"] = best["detour_km"]
            m["detour_pct"] = round(
                best["detour_km"] / m["base_distance_km"] * 100.0, 1
            ) if m["base_distance_km"] > 0 else 0.0
            m["dropoff_gap_km"] = best["dropoff_gap_km"]
            m["last_mile_transit"] = best["last_mile_transit"]
        else:
            m["last_mile_transit"] = transit.transit_option_for_trip(
                m["destination_lat"], m["destination_lon"],
                payload.destination.lat, payload.destination.lon,
                payload.requested_time,
                destination_place=destination_place,
            )

    # Refine the ranking now that some matches may have a smaller,
    # route-aware detour than the naive "all the way to my own destination"
    # figure they were originally ranked on.
    driver_matches.sort(key=lambda d: (d["detour_km"], d["detour_pct"]))

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


# Serve the static frontend last, so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
