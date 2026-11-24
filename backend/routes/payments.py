"""
Paying for a seat (see payments.py, the module these routes call, and
docs/payments.md):

  GET  /api/payments/config          -- whether paying through the site is on,
                                        and the service fee
  GET  /api/payments                 -- what the user paid and received
  POST /api/bookings/{id}/pay        -- the address of a page to pay on
  POST /api/bookings/{id}/pay/check  -- back from that page: what became of it
  POST /api/bookings/{id}/pay/dispute  -- the passenger: the ride didn't take place
  POST /api/payments/webhook         -- Stripe's own calls
"""
import os

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import payments
import reports
from api import Member, Writer

router = APIRouter()


def base_url(request: Request) -> str:
    """Where Stripe sends the passenger back: the site's public address, or
    the one this request came to (a local test)."""
    return (os.environ.get("PUBLIC_BASE_URL") or "").strip() or str(request.base_url)


@router.get("/api/payments/config")
def payments_config():
    on = payments.enabled()
    # fee: {percent, fixed_cts}, for the prices a search shows; null without one.
    return {"enabled": on, "fee": payments.fee_settings() if on else None}


@router.get("/api/payments")
def payment_history(user: Member):
    return payments.history(user)


@router.post("/api/bookings/{booking_id}/pay")
def pay_booking(booking_id: int, request: Request, user: Writer):
    return {"url": payments.checkout(booking_id, user, base_url(request))}


@router.post("/api/bookings/{booking_id}/pay/check")
def check_payment(booking_id: int, user: Writer):
    return payments.check(booking_id, user)


class Dispute(BaseModel):
    details: str | None = Field(default=None, max_length=reports.MAX_DETAILS_CHARS)


@router.post("/api/bookings/{booking_id}/pay/dispute")
def dispute_payment(booking_id: int, user: Writer, payload: Dispute | None = None):
    return payments.dispute(booking_id, user, payload.details if payload else None)


@router.post("/api/payments/webhook")
async def stripe_webhook(request: Request):
    # The signature is over the body exactly as it was sent.
    payload = await request.body()
    return await run_in_threadpool(payments.webhook, payload, request.headers.get("stripe-signature"))
