"""
Paying drivers (see payouts.py, the module these routes call, and
docs/payments.md):

  GET  /api/payouts            -- how far the driver's payout setup is, what
                                  is waiting for them, what was sent
  POST /api/payouts/onboard    -- the address of Stripe's pages to set payouts up
  POST /api/payouts/refresh    -- back from those pages: ask Stripe how far it is
  POST /api/payouts/dashboard  -- the address of the driver's own pages at Stripe
"""
from fastapi import APIRouter, Request

import payouts
from api import Member, Writer
from routes.payments import base_url

router = APIRouter()


@router.get("/api/payouts")
def payout_status(user: Member):
    return payouts.status(user)


@router.post("/api/payouts/onboard")
def set_up_payouts(request: Request, user: Writer):
    return {"url": payouts.onboard(user, base_url(request))}


@router.post("/api/payouts/refresh")
def refresh_payouts(user: Writer):
    return payouts.refresh(user)


@router.post("/api/payouts/dashboard")
def open_payout_dashboard(user: Writer):
    return {"url": payouts.dashboard(user)}
