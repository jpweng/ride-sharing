"""
FastAPI backend for the ride-sharing prototype.

Two core use cases: a driver proposes a drive (routes/drives.py) and a
passenger asks for a ride (routes/match.py). The API's routes live in the
routes/ package, one module per feature area, each with its own
`router`; this module includes every one of them automatically, so a new
feature adds a module there instead of editing this file. What the routes
share (errors, sessions, common request bodies) is in api.py.

This module only creates the app: its startup (database migrations, the
maintenance task), the checks in front of every request, error handlers,
the routes, and the static frontend, served last.

Cross-site request forgery: the session cookie is SameSite=Lax, so
browsers don't send it with cross-site POST/PUT/DELETE requests; on top of
that, every such request to /api/ must come from this site's own pages
(Origin header, or Sec-Fetch-Site), see reject_cross_site_writes.

Every minute, a background task emails the notifications that are due
(notify_email.py). An hourly one keeps a rolling week of fictional demo drives in
the database and deletes visitors' data a month after it's last useful (see
maintenance.py). DEMO_DRIVES=off in the environment removes the demo
drives and hides them everywhere (see demo_drives.py).
"""
import asyncio
import importlib
import logging
import os
import pkgutil
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import auth
import database as db
import demo_drives
import ios_app
import maintenance
import notify_email
import routes
from api import FRONTEND_DIR, api_error

logger = logging.getLogger("uvicorn.error")

MAINTENANCE_INTERVAL_S = 3600
EMAIL_INTERVAL_S = 60

APP_THREADS = int(os.environ.get("APP_THREADS", "16"))


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


async def _email_loop():
    # Notifications are emailed from here, not when they're made, so no
    # request waits for an email to leave (see notify_email.py).
    while True:
        await asyncio.sleep(EMAIL_INTERVAL_S)
        try:
            counts = await asyncio.to_thread(notify_email.send_pending)
            if counts["sent"] or counts["failed"]:
                logger.info("Notification emails: %s", counts)
        except Exception:
            logger.exception("Sending notification emails failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Requests are handled in a thread pool (the endpoints are synchronous):
    # fewer threads than the default 40, which in the load test only added
    # memory, each holding a request's data. Enough to wait on
    # transport.opendata.ch for several requests at once.
    anyio.to_thread.current_default_thread_limiter().total_tokens = APP_THREADS
    if os.environ.get("EMAIL_BACKEND") == "ses" and not os.environ.get("PUBLIC_BASE_URL"):
        logger.warning("PUBLIC_BASE_URL is not set: no sign-in links can be sent")
    tasks = [asyncio.create_task(_maintenance_loop()), asyncio.create_task(_email_loop())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Ride and Rail", lifespan=lifespan)

db.init_db()


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


@app.middleware("http")
async def without_demo_in_the_ios_app(request: Request, call_next):
    # The iOS app is shown the site without its demo (ios_app.py).
    if ios_app.is_app(request):
        demo_drives.hide_for_this_request()
    return await call_next(request)


# ---------------------------------------------------------------------
# Routes: every module of the routes package, in name order
# ---------------------------------------------------------------------
def route_modules() -> list:
    return [
        importlib.import_module(f"{routes.__name__}.{info.name}")
        for info in sorted(pkgutil.iter_modules(routes.__path__), key=lambda info: info.name)
    ]


for module in route_modules():
    app.include_router(module.router)


# Serve the static frontend last, so it doesn't shadow the routes above.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
