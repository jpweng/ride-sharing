"""
Ratings after a ride and reports of a problem (see ratings.py, reports.py):

  POST /api/bookings/{id}/rating  -- rate the other side of a booked ride
  POST /api/bookings/{id}/report  -- report the other side to the team
"""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

import ratings
import reports
from api import Writer

router = APIRouter()


class Rating(BaseModel):
    stars: int = Field(ge=ratings.MIN_STARS, le=ratings.MAX_STARS)


class Report(BaseModel):
    reason: Literal["no_show", "late", "unsafe", "behaviour", "other"]
    details: str | None = Field(default=None, max_length=reports.MAX_DETAILS_CHARS)


@router.post("/api/bookings/{booking_id}/rating")
def rate_ride(booking_id: int, payload: Rating, user: Writer):
    return ratings.rate(booking_id, user, payload.stars)


@router.post("/api/bookings/{booking_id}/report")
def report_ride(booking_id: int, payload: Report, user: Writer):
    report = reports.file(booking_id, user, payload.reason, payload.details)
    # What the reporter gets back: that it's filed, nothing about the other one.
    return {"id": report["id"], "reason": report["reason"]}
