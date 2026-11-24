"""
Paying for a seat (see payments.py, the module these routes call, and
docs/payments.md):

  GET  /api/payments/config          -- whether paying through the site is on
  POST /api/bookings/{id}/pay        -- the address of a page to pay on
  POST /api/bookings/{id}/pay/check  -- back from that page: what became of it
  POST /api/payments/webhook         -- Stripe's own calls
"""
import os

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

import payments
from api import Writer

router = APIRouter()


def _base_url(request: Request) -> str:
    """Where Stripe sends the passenger back: the site's public address, or
    the one this request came to (a local test)."""
    return (os.environ.get("PUBLIC_BASE_URL") or "").strip() or str(request.base_url)


@router.get("/api/payments/config")
def payments_config():
    return {"enabled": payments.enabled()}


@router.post("/api/bookings/{booking_id}/pay")
def pay_booking(booking_id: int, request: Request, user: Writer):
    return {"url": payments.checkout(booking_id, user, _base_url(request))}


@router.post("/api/bookings/{booking_id}/pay/check")
def check_payment(booking_id: int, user: Writer):
    return payments.check(booking_id, user)


@router.post("/api/payments/webhook")
async def stripe_webhook(request: Request):
    # The signature is over the body exactly as it was sent.
    payload = await request.body()
    return await run_in_threadpool(payments.webhook, payload, request.headers.get("stripe-signature"))
