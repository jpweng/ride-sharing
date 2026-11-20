"""
What a driver's map shows for a ride request they were told about:

  GET /api/notifications/{id}/preview  -- the request's two ends and, when
                                          the driver was chosen for a trip
                                          they usually make, that trip's
                                          road route with the pickup and
                                          drop-off on it

Only the notification's recipient (signed in, profile complete) may ask;
read-only. Errors: 404 notification_not_found (no such notification, or not
the caller's), 404 request_not_open (the request is closed or gone).
"""
from fastapi import APIRouter

import database as db
import matching
from api import Member, api_error

router = APIRouter()

_REQUEST_FIELDS = ("origin_name", "origin_lat", "origin_lon",
                   "destination_name", "destination_lat", "destination_lon", "requested_time")


def _usual_route(trip: dict) -> list[list[float]]:
    """The usual trip's road route as [[lat, lon], ...], thinned as for
    matches (matching.prepare_route); a straight line when the routing
    service can't be reached (matching.route_with_fallback)."""
    origin, destination = trip["origin"], trip["destination"]
    route = matching.route_with_fallback([(origin["lat"], origin["lon"]), (destination["lat"], destination["lon"])])
    drive = {"origin_lat": origin["lat"], "origin_lon": origin["lon"],
             "destination_lat": destination["lat"], "destination_lon": destination["lon"]}
    return [[lat, lon] for lat, lon, _ in matching.prepare_route(route["geometry"], drive)]


@router.get("/api/notifications/{notification_id}/preview")
def preview_notification(notification_id: int, user: Member):
    n = db.get_notification(notification_id)
    if not n or n["recipient_id"] != user["id"] or n["kind"] != "request_for_driver":
        raise api_error(404, "notification_not_found", "Notification not found")
    request = db.get_open_request(n["request_id"])
    if request is None or request["status"] != "open":
        raise api_error(404, "request_not_open", "This ride request is no longer open")
    out = {"request": {k: request[k] for k in _REQUEST_FIELDS}}
    details = n.get("details") or {}
    trip = details.get("trip")
    if trip:
        out["usual_trip"] = {
            "origin": trip["origin"], "destination": trip["destination"],
            "route": _usual_route(trip),
            "pickup": details["pickup"], "dropoff": details["dropoff"],
            "suggested_departure": details.get("suggested_departure"),
        }
    return out
