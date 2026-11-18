"""
FastAPI backend for the ride-sharing prototype.

Two core use cases:
  1. POST /api/drives      -- a driver proposes a drive, at a price per
                               passenger inside the band (see pricing.py).
  GET /api/price_band      -- that band for a trip, before posting.
  2. POST /api/match       -- a passenger asks for a ride; the app finds
                               the drives whose route passes within their
                               pickup and drop-off radii, around their time
                               (see matching.py) and a few just outside
                               them, adds a last-mile transit connection
                               from the drop-off for the best ones, and
                               also offers a pure-public-transport option
                               (see transit.py).
  GET /api/drives          -- browse all upcoming posted drives
                               (?mine=true: the signed-in driver's own).
                               Drives and matches say whether they're
                               "bookable": demo drives aren't.
  GET /api/drives/{id}     -- a single drive with full route geometry.
  DELETE /api/drives/{id}  -- a driver cancels one of their drives.
  GET /api/ride_requests   -- the signed-in passenger's recent searches.
  POST /api/requests       -- a passenger asks for a ride nobody has posted;
                               a few drivers who made the trip before are
                               told (see notify.py).
  GET/DELETE /api/requests -- a passenger's open ride requests; cancel one.
  GET /api/notifications   -- a user's inbox; .../read, .../{id}/action and
                               /api/notification_settings act on it.
  /api/auth/*, /api/account -- sign-in by email link, sessions, account
                               deletion (see auth.py).

Who you are comes from the session cookie only (auth.py): browsing and
searching are public; posting, cancelling and your own drives, searches,
requests and inbox need a signed-in user. "View as a demo driver" sessions
can read their demo driver's pages but not change anything.

Cross-site request forgery: the session cookie is SameSite=Lax, so
browsers don't send it with cross-site POST/PUT/DELETE requests; on top of
that, every such request to /api/ must come from this site's own pages
(Origin header, or Sec-Fetch-Site), see reject_cross_site_writes.

An hourly background task keeps a rolling week of fictional demo drives in
the database and deletes visitors' data a month after it's last useful (see
maintenance.py). DEMO_DRIVES=off in the environment removes the demo
drives and hides them everywhere (see demo_drives.py).
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

import anyio.to_thread
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import database as db
import demo_drives
import mailer
import maintenance
import matching
import notify
import pricing
import search
import transit

logger = logging.getLogger("uvicorn.error")

MAINTENANCE_INTERVAL_S = 3600

APP_THREADS = int(os.environ.get("APP_THREADS", "16"))

# Where the site is, for the links in emails. Never taken from the request's
# Host header: a forged one would send people links to another site.
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")

# The session cookie is Secure unless COOKIE_SECURE=0 (only for testing over
# plain HTTP on another host than localhost: browsers accept Secure cookies
# on http://localhost). Secure cookies get the __Host- prefix: no Domain, path
# /, HTTPS only, so a subdomain can't set or overwrite it.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"
SESSION_COOKIE = "__Host-session" if COOKIE_SECURE else "session"


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
    # Requests are handled in a thread pool (the endpoints are synchronous):
    # fewer threads than the default 40, which in the load test only added
    # memory, each holding a request's data. Enough to wait on
    # transport.opendata.ch for several requests at once.
    anyio.to_thread.current_default_thread_limiter().total_tokens = APP_THREADS
    if os.environ.get("EMAIL_BACKEND") == "ses" and not os.environ.get("PUBLIC_BASE_URL"):
        logger.warning("PUBLIC_BASE_URL is not set: no sign-in links can be sent")
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


@app.exception_handler(auth.AuthError)
def _auth_error(request: Request, e: auth.AuthError):
    return JSONResponse({"detail": {"code": e.code, "message": e.message}}, status_code=e.status)


# ---------------------------------------------------------------------
# Cross-site request forgery
# ---------------------------------------------------------------------
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(request: Request) -> bool:
    """Whether a request comes from this site's own pages. Browsers send
    Origin with every POST/PUT/DELETE (and Sec-Fetch-Site with every
    request); a request with neither isn't from a browser, so it has no
    victim's cookie to abuse."""
    origin = request.headers.get("origin")
    if origin is not None:
        return origin != "null" and urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in ("same-origin", "none")
    return True


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/") and not _same_origin(request):
        e = api_error(403, "cross_site_request", "Requests that change data must come from this site")
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)


# ---------------------------------------------------------------------
# Sessions: who is asking
# ---------------------------------------------------------------------
def _set_session_cookie(response: Response, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(ttl.total_seconds()),
        path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")


def current_session(request: Request, response: Response) -> dict | None:
    """The request's session ({"session", "user"}), or None."""
    token = request.cookies.get(SESSION_COOKIE)
    found = auth.session_for(token)
    if found and found["extended"]:
        _set_session_cookie(response, token, auth.SESSION_TTL)  # sliding expiry
    return found


def signed_in(found: Annotated[dict | None, Depends(current_session)]) -> dict:
    """A session, whether or not the user has finished their profile."""
    if found is None:
        raise api_error(401, "not_signed_in", "Sign in first")
    return found


def member(found: Annotated[dict, Depends(signed_in)]) -> dict:
    """The signed-in user, with their profile complete (name, 18+, code of
    conduct). Demo drivers' sessions count: they can read their pages."""
    user = dict(found["user"])
    if not auth.profile_complete(user):
        raise api_error(403, "profile_incomplete", "Complete your profile first")
    user["is_demo"] = bool(user["is_demo"] or found["session"]["is_demo"])
    return user


def writer(user: Annotated[dict, Depends(member)]) -> dict:
    """A signed-in user who may change things: not a demo driver's session."""
    if user["is_demo"]:
        raise api_error(403, "demo_read_only", "Viewing as a demo driver: nothing can be changed")
    return user


Member = Annotated[dict, Depends(member)]
Writer = Annotated[dict, Depends(writer)]


# ---------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------
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
    origin: LatLon
    destination: LatLon
    departure_time: str  # ISO 8601
    seats_available: int = Field(default=1, ge=1, le=8)
    # The driver's price per passenger, for one passenger and the whole
    # drive, inside pricing.band(); the default suggestion if omitted.
    price_chf: float | None = Field(default=None, ge=0, le=1000)


class MatchRequest(BaseModel):
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
    action: Literal["offer", "decline", "mute", "view"]


class NotificationSettings(BaseModel):
    notify_requests: bool | None = None
    resume: bool = False  # turn notifications back on after the back-off paused them


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


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------
# Sign-in, profile, sign-out, account deletion (see auth.py)
# ---------------------------------------------------------------------
@app.post("/api/auth/link")
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


@app.post("/api/auth/verify")
def verify_sign_in_link(payload: SignInVerify, request: Request, response: Response):
    user, token = auth.verify(payload.token)
    auth.end_session(request.cookies.get(SESSION_COOKIE))  # a previous one in this browser
    _set_session_cookie(response, token, auth.SESSION_TTL)
    return {"user": auth.public_user(user)}


@app.get("/api/auth/me")
def who_am_i(found: Annotated[dict | None, Depends(current_session)]):
    return {"user": auth.public_user(found["user"]) if found else None}


@app.post("/api/auth/profile")
def complete_profile(payload: ProfileUpdate, found: Annotated[dict, Depends(signed_in)]):
    if found["session"]["is_demo"]:
        raise api_error(403, "demo_read_only", "Viewing as a demo driver: nothing can be changed")
    user = auth.complete_profile(found["user"], payload.first_name, payload.last_name,
                                 payload.adult, payload.accept_conduct)
    return {"user": auth.public_user(user)}


@app.post("/api/auth/sign_out")
def sign_out(request: Request, response: Response):
    auth.end_session(request.cookies.get(SESSION_COOKIE))
    _clear_session_cookie(response)
    return {"signed_out": True}


@app.get("/api/auth/demo_drivers")
def list_demo_drivers():
    return auth.demo_drivers()


@app.post("/api/auth/demo")
def view_as_demo_driver(payload: DemoSignIn, request: Request, response: Response):
    user, token = auth.demo_session(payload.driver_name)
    auth.end_session(request.cookies.get(SESSION_COOKIE))
    _set_session_cookie(response, token, auth.DEMO_SESSION_TTL)
    return {"user": auth.public_user(user)}


@app.delete("/api/account")
def delete_account(response: Response, found: Annotated[dict, Depends(signed_in)]):
    user = found["user"]
    if user["is_demo"] or found["session"]["is_demo"]:
        raise api_error(403, "demo_read_only", "Viewing as a demo driver: nothing can be changed")
    # Passengers who were told about the user's upcoming drives hear they're cancelled.
    for drive in db.list_drives(upcoming_only=True, driver_name=user["name"], with_geometry=False):
        notify.tell_drive_cancelled(drive)
    deleted = db.delete_account(user["id"])
    logger.info("Account deleted: %s", {k: v for k, v in deleted.items() if v})
    _clear_session_cookie(response)
    return {"deleted": True}


# ---------------------------------------------------------------------
# Drives
# ---------------------------------------------------------------------
@app.post("/api/drives")
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


@app.get("/api/price_band")
def get_price_band(
    origin_lat: Annotated[float, Query(ge=-90, le=90)],
    origin_lon: Annotated[float, Query(ge=-180, le=180)],
    destination_lat: Annotated[float, Query(ge=-90, le=90)],
    destination_lon: Annotated[float, Query(ge=-180, le=180)],
):
    # The driver's form asks before posting: the same road route as
    # POST /api/drives computes, so the same band.
    route = matching.route_with_fallback([(origin_lat, origin_lon), (destination_lat, destination_lon)])
    km = route["distance_m"] / 1000.0
    price_band = pricing.band(km)
    if not price_band:
        raise api_error(400, "no_distance", "Start and destination are the same place")
    return {
        **price_band,
        # Each passenger's ceiling with 1, 2, ... 8 passengers: why the
        # price per passenger falls when more people join.
        "ceilings_chf": [pricing.ceiling_chf(km, n) for n in range(1, 9)],
    }


@app.get("/api/drives")
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
    return [search.with_bookable(pricing.with_prices(d)) for d in drives]


@app.get("/api/drives/{drive_id}")
def get_drive(drive_id: int):
    drive = db.get_drive(drive_id)
    if not drive or (drive["is_demo"] and not demo_drives.enabled()):
        raise api_error(404, "drive_not_found", "Drive not found")
    return search.with_bookable(pricing.with_prices(drive))


@app.delete("/api/drives/{drive_id}")
def cancel_drive(drive_id: int, user: Writer):
    drive = db.get_drive(drive_id)
    if not drive:
        raise api_error(404, "drive_not_found", "Drive not found")
    if drive["driver_id"] != user["id"]:
        raise api_error(403, "not_your_drive", "Only the driver who posted this drive can cancel it")
    notify.tell_drive_cancelled(drive)
    db.delete_drive(drive_id)
    return {"deleted": drive_id}


# ---------------------------------------------------------------------
# Searches and ride requests
# ---------------------------------------------------------------------
@app.get("/api/ride_requests")
def get_ride_requests(user: Member):
    return db.list_recent_ride_requests(user["name"])


@app.post("/api/match")
def match_ride(payload: MatchRequest, found: Annotated[dict | None, Depends(current_session)] = None):
    try:
        datetime.fromisoformat(payload.requested_time)
    except ValueError:
        raise api_error(400, "invalid_time", "requested_time must be ISO 8601, e.g. 2026-09-22T08:30:00")

    # Searching is public; a signed-in passenger's search is kept for their
    # "recent searches" (and ride predictions later). Not demo sessions'.
    user = found["user"] if found else None
    if user and auth.profile_complete(user) and not (user["is_demo"] or found["session"]["is_demo"]):
        db.save_ride_request(
            passenger_name=user["name"],
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

    driver_matches, outside_matches = search.with_outside(
        passenger_origin, passenger_destination, payload.requested_time,
        pickup_radius_km=payload.pickup_radius_km,
        dropoff_radius_km=payload.dropoff_radius_km,
        time_tolerance_min=payload.time_tolerance_min,
    )

    # What the passenger pays for their part of the drive, and the train
    # fare for it (see pricing.py).
    driver_matches = [pricing.with_ride_prices(m) for m in driver_matches]
    outside_matches = [pricing.with_ride_prices(m) for m in outside_matches]

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
        # Just outside the passenger's circles or time flexibility, marked
        # as such: they may like them anyway. No last-mile lookups.
        "outside_matches": outside_matches,
        "transit_option": transit_option,
    }


@app.post("/api/requests")
def create_ride_request(payload: RideRequestCreate, user: Writer):
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
        user["name"], payload.origin.model_dump(), payload.destination.model_dump(),
        asked.isoformat(timespec="minutes"),
        payload.pickup_radius_km, payload.dropoff_radius_km, payload.time_tolerance_min,
    )
    told = notify.notify_drivers(request)
    # A real passenger's request only reaches real drivers, so the count is
    # of real drivers. A demo request (made under a demo driver's name) only
    # reaches demo drivers, and gets their (fictional) names, so the demo
    # can go on with signing in as one of them.
    result = {"request": request, "drivers_told": len(told), "demo_request": notify.is_demo_request(request)}
    if result["demo_request"]:
        result["demo_drivers_told"] = told
    return result


@app.get("/api/requests")
def get_ride_requests_open(user: Member):
    return db.list_open_requests(notify.local_now().isoformat(timespec="minutes"), user["name"])


@app.delete("/api/requests/{request_id}")
def cancel_ride_request(request_id: int, user: Writer):
    request = db.get_open_request(request_id)
    if not request:
        raise api_error(404, "request_not_found", "Ride request not found")
    if request["passenger_id"] != user["id"]:
        raise api_error(403, "not_your_request", "Only the passenger who made this request can cancel it")
    db.set_request_status(request_id, "cancelled")
    return {"cancelled": request_id}


# ---------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------
@app.get("/api/notifications")
def get_notifications(user: Member):
    return notify.inbox(user["name"], demo_view=user["is_demo"])


@app.post("/api/notifications/read")
def read_notifications(user: Member):
    # Allowed when viewing as a demo driver: reading changes nothing anyone
    # else sees (it isn't an answer, see notify.backoff).
    return {"read": db.mark_notifications_read(user["name"])}


@app.post("/api/notifications/{notification_id}/action")
def act_on_notification(notification_id: int, payload: NotificationAction, user: Writer):
    n = db.get_notification(notification_id)
    if not n or n["recipient_id"] != user["id"]:
        raise api_error(404, "notification_not_found", "Notification not found")
    if payload.action == "mute":
        request = db.get_open_request(n["request_id"])
        if request:
            db.add_route_mute(
                user["name"],
                (request["origin_lat"], request["origin_lon"]),
                (request["destination_lat"], request["destination_lon"]),
            )
    db.set_notification_action(notification_id, payload.action)
    return db.get_notification(notification_id)


@app.put("/api/notification_settings")
def update_notification_settings(payload: NotificationSettings, user: Writer):
    db.update_user_settings(user["name"], payload.notify_requests, reset_backoff=payload.resume)
    return notify.inbox(user["name"])


# Serve the static frontend last, so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
