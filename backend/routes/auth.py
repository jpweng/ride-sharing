"""
Sign-in by email link, profile, sign-out, the demo-driver view and account
deletion (see auth.py, the module these routes call):

  POST /api/auth/link, /api/auth/verify -- sign in by email link
  GET /api/auth/me                       -- who is signed in
  POST /api/auth/profile                 -- name, 18+, code of conduct
  POST /api/auth/sign_out
  GET /api/auth/demo_drivers, POST /api/auth/demo -- view as a demo driver
  DELETE /api/account
"""
import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

import auth
import bookings
import database as db
import mailer
import notify
import social
from api import (
    SESSION_COOKIE, _clear_session_cookie, _set_session_cookie, api_error, current_session, signed_in,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter()

# Where the site is, for the links in emails. Never taken from the request's
# Host header: a forged one would send people links to another site.
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")


class SignInLinkRequest(BaseModel):
    email: str = Field(max_length=auth.MAX_EMAIL_LENGTH)
    language: str = "en"  # of the email


class SignInVerify(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class ProfileUpdate(BaseModel):
    first_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)
    adult: bool = False  # "I am 18 or older"
    accept_conduct: bool = False  # the code of conduct, version auth.CONDUCT_VERSION


class DemoSignIn(BaseModel):
    driver_name: str


@router.post("/api/auth/link")
def request_sign_in_link(payload: SignInLinkRequest, request: Request):
    # The same answer whether or not the email has an account, and whether
    # or not an email was actually sent (see auth.request_link).
    if os.environ.get("EMAIL_BACKEND") == "ses" and not os.environ.get("PUBLIC_BASE_URL"):
        logger.error("PUBLIC_BASE_URL is not set: no sign-in link sent")
        raise api_error(503, "email_unavailable", "The sign-in email couldn't be sent; try again later")
    try:
        auth.request_link(payload.email, payload.language, request.client.host if request.client else None,
                          PUBLIC_BASE_URL)
    except mailer.EmailError:
        logger.exception("Sending a sign-in link failed")
        raise api_error(503, "email_unavailable", "The sign-in email couldn't be sent; try again later")
    return {"ok": True}


@router.post("/api/auth/verify")
def verify_sign_in_link(payload: SignInVerify, request: Request, response: Response):
    user, token = auth.verify(payload.token)
    auth.end_session(request.cookies.get(SESSION_COOKIE))  # a previous one in this browser
    _set_session_cookie(response, token, auth.SESSION_TTL)
    return {"user": auth.public_user(user)}


@router.get("/api/auth/me")
def who_am_i(found: Annotated[dict | None, Depends(current_session)]):
    if not found:
        return {"user": None}
    user = auth.public_user(found["user"])
    if not user["name"]:
        # First sign-in with Google or Apple: the name they gave, to fill in
        # the profile page (the user can change it there).
        user["suggested_name"] = social.suggested_name(found["user"]["id"])
    return {"user": user}


@router.post("/api/auth/profile")
def complete_profile(payload: ProfileUpdate, found: Annotated[dict, Depends(signed_in)]):
    if found["session"]["is_demo"]:
        raise api_error(403, "demo_read_only", "Viewing as a demo driver: nothing can be changed")
    user = auth.complete_profile(found["user"], payload.first_name, payload.last_name,
                                 payload.adult, payload.accept_conduct)
    return {"user": auth.public_user(user)}


@router.post("/api/auth/sign_out")
def sign_out(request: Request, response: Response):
    auth.end_session(request.cookies.get(SESSION_COOKIE))
    _clear_session_cookie(response)
    return {"signed_out": True}


@router.get("/api/auth/demo_drivers")
def list_demo_drivers():
    return auth.demo_drivers()


@router.post("/api/auth/demo")
def view_as_demo_driver(payload: DemoSignIn, request: Request, response: Response):
    user, token = auth.demo_session(payload.driver_name)
    auth.end_session(request.cookies.get(SESSION_COOKIE))
    _set_session_cookie(response, token, auth.DEMO_SESSION_TTL)
    return {"user": auth.public_user(user)}


@router.delete("/api/account")
def delete_account(response: Response, found: Annotated[dict, Depends(signed_in)]):
    user = found["user"]
    if user["is_demo"] or found["session"]["is_demo"]:
        raise api_error(403, "demo_read_only", "Viewing as a demo driver: nothing can be changed")
    # Passengers with a seat on the user's upcoming drives, or who were told
    # about them, hear they're cancelled; drivers the user had asked for a
    # seat hear it's free again.
    for drive in db.list_drives(upcoming_only=True, driver_name=user["name"], with_geometry=False):
        booked = bookings.tell_drive_cancelled(drive)
        notify.tell_drive_cancelled(drive, exclude=booked)
    bookings.tell_account_deleted(user)
    deleted = db.delete_account(user["id"])
    logger.info("Account deleted: %s", {k: v for k, v in deleted.items() if v})
    _clear_session_cookie(response)
    return {"deleted": True}
