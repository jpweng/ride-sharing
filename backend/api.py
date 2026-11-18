"""
What the API's routes (routes/*.py) share: error answers, who is asking
(the session cookie, see auth.py) and the request bodies several routes
take.

Who you are comes from the session cookie only: browsing and searching are
public; posting, cancelling and your own drives, searches, requests and
inbox need a signed-in user. "View as a demo driver" sessions can read
their demo driver's pages but not change anything. A route asks for its
user with a parameter typed Member (signed in, profile complete) or Writer
(and may change things).
"""
import os
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel

import auth

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# The session cookie is Secure unless COOKIE_SECURE=0 (only for testing over
# plain HTTP on another host than localhost: browsers accept Secure cookies
# on http://localhost). Secure cookies get the __Host- prefix: no Domain, path
# /, HTTPS only, so a subdomain can't set or overwrite it.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"
SESSION_COOKIE = "__Host-session" if COOKIE_SECURE else "session"


def api_error(status: int, code: str, message: str) -> HTTPException:
    # The frontend shows its translation of `code` (errors.<code> in
    # frontend/i18n/<lang>/errors.json); `message` is for API users and logs.
    return HTTPException(status, {"code": code, "message": message})


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
# Request bodies shared by several routes
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
