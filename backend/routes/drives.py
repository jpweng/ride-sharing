"""
Drives:

  POST /api/drives         -- a driver proposes a drive, at a price per
                              passenger inside the band (see pricing.py).
  GET /api/drives          -- browse all upcoming posted drives
                              (?mine=true: the signed-in driver's own).
                              Drives say whether they're "bookable": demo
                              drives aren't. seats_available is the free
                              seats, seats_booked those taken (bookings.py).
  GET /api/drives/{id}     -- a single drive with full route geometry.
  DELETE /api/drives/{id}  -- a driver cancels one of their drives; its
                              passengers are told.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

import bookings
import database as db
import demo_drives
import matching
import notify
import pricing
import ratings
import search
from api import api_error, current_session, is_member, LatLon, member, signed_in, without_driver, Writer

router = APIRouter()


class DriveCreate(BaseModel):
    origin: LatLon
    destination: LatLon
    departure_time: str  # ISO 8601
    seats_available: int = Field(default=1, ge=1, le=8)
    # The driver's price per passenger, for one passenger and the whole
    # drive, inside pricing.band(); the default suggestion if omitted.
    price_chf: float | None = Field(default=None, ge=0, le=1000)


@router.post("/api/drives")
def post_drive(payload: DriveCreate, user: Writer):
    try:
        datetime.fromisoformat(payload.departure_time)
    except ValueError:
        raise api_error(400, "invalid_time", "departure_time must be ISO 8601, e.g. 2026-09-22T08:30:00")

    route = matching.route_with_fallback([
        (payload.origin.lat, payload.origin.lon),
        (payload.destination.lat, payload.destination.lon),
    ])

    price_band = pricing.band(route["distance_m"] / 1000.0)
    driver_price = None
    if price_band:
        driver_price = price_band["default_chf"]
        if payload.price_chf is not None:
            driver_price = round(payload.price_chf, 2)
            problem = pricing.check_driver_price(driver_price, price_band)
            if problem == "price_too_high":
                raise api_error(400, "price_too_high",
                                f"The price per passenger can be at most CHF {price_band['max_chf']:.2f}")
            if problem == "price_too_low":
                raise api_error(400, "price_too_low",
                                f"The price per passenger must be at least CHF {price_band['min_chf']:.2f}")

    drive_id = db.create_drive(
        driver_name=user["name"],
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
        driver_price_chf=driver_price,
    )
    drive = db.get_drive(drive_id)
    # Passengers waiting for a ride like this one are told.
    notify.tell_passengers(drive)
    return search.with_bookable(pricing.with_prices(drive))


@router.get("/api/drives")
def get_drives(
    found: Annotated[dict | None, Depends(current_session)],
    upcoming_only: bool = True,
    mine: bool = False,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
):
    # mine=true: the signed-in driver's own drives (their Driver page).
    driver_name = member(signed_in(found))["name"] if mine else None
    # Without route geometry, which can be ~60 KB per drive: the map gets it
    # from /api/drives/{id} for the drive it shows.
    drives = db.list_drives(
        upcoming_only=upcoming_only, driver_name=driver_name, with_geometry=False, limit=limit,
        include_demo=demo_drives.enabled(),
    )
    drives = ratings.with_driver_ratings(bookings.with_seats_booked(drives))
    drives = [search.with_bookable(pricing.with_prices(d)) for d in drives]
    # Public, but who drives is for signed-in users.
    return drives if is_member(found) else [without_driver(d) for d in drives]


@router.get("/api/drives/{drive_id}")
def get_drive(drive_id: int, found: Annotated[dict | None, Depends(current_session)] = None):
    drive = db.get_drive(drive_id)
    if not drive or (drive["is_demo"] and not demo_drives.enabled()):
        raise api_error(404, "drive_not_found", "Drive not found")
    drive = ratings.with_driver_ratings(bookings.with_seats_booked([drive]))[0]
    drive = search.with_bookable(pricing.with_prices(drive))
    return drive if is_member(found) else without_driver(drive)


@router.delete("/api/drives/{drive_id}")
def cancel_drive(drive_id: int, user: Writer):
    drive = db.get_drive(drive_id)
    if not drive:
        raise api_error(404, "drive_not_found", "Drive not found")
    if drive["driver_id"] != user["id"]:
        raise api_error(403, "not_your_drive", "Only the driver who posted this drive can cancel it")
    # Passengers with a request or a seat on it, then those who were only
    # told about it. The drive's bookings are deleted with it.
    booked = bookings.tell_drive_cancelled(drive)
    notify.tell_drive_cancelled(drive, exclude=booked)
    db.delete_drive(drive_id)
    return {"deleted": drive_id}
