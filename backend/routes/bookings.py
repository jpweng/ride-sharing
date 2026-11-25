"""
Bookings (see bookings.py):

  POST /api/drives/{id}/bookings      -- a passenger asks for seats on a drive
  GET /api/bookings                   -- the user's bookings, as a passenger
                                         and as a driver
  POST /api/bookings/{id}/accept      -- the driver accepts a request
  POST /api/bookings/{id}/decline     -- the driver declines it
  POST /api/bookings/{id}/cancel      -- the passenger or the driver cancels
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

import bookings
import database as db
import demo_drives
import payments
import ratings
from api import LatLon, Member, Writer, api_error

router = APIRouter()


class BookingPlace(LatLon):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class BookingCreate(BaseModel):
    # The passenger's own start and destination, as in the search that found
    # the drive; neither for the whole drive.
    origin: BookingPlace | None = None
    destination: BookingPlace | None = None
    seats: int = Field(default=1, ge=1, le=bookings.MAX_SEATS)
    note: str | None = Field(default=None, max_length=bookings.MAX_NOTE_CHARS)

    @model_validator(mode="after")
    def _both_ends_or_neither(self):
        if (self.origin is None) != (self.destination is None):
            raise ValueError("give both origin and destination, or neither")
        return self


class BookingAnswer(BaseModel):
    note: str | None = Field(default=None, max_length=bookings.MAX_NOTE_CHARS)


@router.post("/api/drives/{drive_id}/bookings")
def request_booking(drive_id: int, payload: BookingCreate, user: Writer):
    drive = db.get_drive(drive_id)
    if not drive or (drive["is_demo"] and not demo_drives.shown()):
        raise api_error(404, "drive_not_found", "Drive not found")
    return bookings.request(
        drive, user,
        payload.origin.model_dump() if payload.origin else None,
        payload.destination.model_dump() if payload.destination else None,
        payload.seats, payload.note,
    )


@router.get("/api/bookings")
def get_bookings(user: Member):
    # Each with what the other side was rated on earlier rides, and its
    # payment when seats are paid through the site.
    return payments.with_payments(ratings.with_booking_ratings(bookings.for_user(user)))


@router.post("/api/bookings/{booking_id}/accept")
def accept_booking(booking_id: int, user: Writer, payload: BookingAnswer | None = None):
    booking = bookings.accept(booking_id, user, payload.note if payload else None)
    payments.booking_accepted(booking)  # from now on the passenger has it to pay
    return booking


@router.post("/api/bookings/{booking_id}/decline")
def decline_booking(booking_id: int, user: Writer):
    return bookings.decline(booking_id, user)


@router.post("/api/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: int, user: Writer):
    booking = bookings.cancel(booking_id, user)
    # Nothing is due any more; what was paid goes back, all of it unless
    # the passenger cancelled late.
    payments.booking_ended(booking_id, by="driver" if booking["status"] == "cancelled_by_driver" else "passenger")
    return booking
