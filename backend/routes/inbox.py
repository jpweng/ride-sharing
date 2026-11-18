"""
The inbox (see notify.py):

  GET /api/notifications                       -- a user's inbox
  POST /api/notifications/read                 -- mark it read
  POST /api/notifications/{id}/action          -- offer, decline, mute, view
  PUT /api/notification_settings               -- requests on/off, resume
"""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

import database as db
import notify
from api import Member, Writer, api_error

router = APIRouter()


class NotificationAction(BaseModel):
    action: Literal["offer", "decline", "mute", "view"]


class NotificationSettings(BaseModel):
    notify_requests: bool | None = None
    resume: bool = False  # turn notifications back on after the back-off paused them


@router.get("/api/notifications")
def get_notifications(user: Member):
    return notify.inbox(user["name"], demo_view=user["is_demo"])


@router.post("/api/notifications/read")
def read_notifications(user: Member):
    # Allowed when viewing as a demo driver: reading changes nothing anyone
    # else sees (it isn't an answer, see notify.backoff).
    return {"read": db.mark_notifications_read(user["name"])}


@router.post("/api/notifications/{notification_id}/action")
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


@router.put("/api/notification_settings")
def update_notification_settings(payload: NotificationSettings, user: Writer):
    db.update_user_settings(user["name"], payload.notify_requests, reset_backoff=payload.resume)
    return notify.inbox(user["name"])
