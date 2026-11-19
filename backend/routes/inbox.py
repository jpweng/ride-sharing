"""
The inbox (see notify.py):

  GET /api/notifications                       -- a user's inbox
  POST /api/notifications/read                 -- mark it read
  POST /api/notifications/{id}/action          -- offer, decline, mute, view
  PUT /api/notification_settings               -- requests on/off, emails
                                                  on/off, resume
  GET /unsubscribe/{token}                     -- the emails' "turn these
                                                  off" link (notify_email.py)
"""
from typing import Literal

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import database as db
import mailer
import notify
import notify_email
from api import Member, Writer, api_error

router = APIRouter()


class NotificationAction(BaseModel):
    action: Literal["offer", "decline", "mute", "view"]


class NotificationSettings(BaseModel):
    notify_requests: bool | None = None
    notify_email: bool | None = None  # also send notifications by email
    resume: bool = False  # turn notifications back on after the back-off paused them


def _inbox(user: dict) -> dict:
    """The inbox with the user's settings, the email switch included."""
    return {
        **notify.inbox(user["name"], demo_view=user["is_demo"]),
        "notify_email": notify_email.enabled_for(user["id"]),
    }


@router.get("/api/notifications")
def get_notifications(user: Member):
    return _inbox(user)


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
    if payload.notify_email is not None:
        notify_email.set_enabled(user["id"], payload.notify_email)
    return _inbox(user)


def _unsubscribe_page(language: str, title: str, body: str, action: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="{language}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="robots" content="noindex, nofollow" />
<title>{title} — Ride and Rail</title>
</head>
<body style="font-family: sans-serif; line-height: 1.5; max-width: 32em; margin: 3em auto; padding: 0 1em">
<h1 style="font-size: 1.3em">{title}</h1>
<p>{body}</p>
{action}
</body>
</html>
""")


def _unsubscribe_language(token: str) -> str:
    user = notify_email.user_for_token(token)
    return user["language"] if user and user["language"] in mailer.LANGUAGES else "en"


@router.get("/unsubscribe/{token}", include_in_schema=False)
def unsubscribe_page(token: str):
    """The link at the bottom of every notification email. Opening it only
    asks: the button turns the emails off (a POST), so a mail scanner that
    opens every link doesn't do it for the user. The same page for an
    unknown token: nothing to learn from trying."""
    language = _unsubscribe_language(token)

    def t(key):
        return escape(mailer.text(language, f"email.unsubscribe.{key}"))

    button = (
        f'<form method="post"><button type="submit" style="padding: 10px 18px; background: #0f6e5f; color: #fff;'
        f' border: 0; border-radius: 6px; font-size: 1em; cursor: pointer">{t("confirm")}</button></form>'
    )
    return _unsubscribe_page(language, t("title"), t("question"), button)


@router.post("/unsubscribe/{token}", include_in_schema=False)
def unsubscribe(token: str):
    """Turns notification emails off for the token's user, without signing
    in: whoever has the email has the link. Outside /api/, so the check on
    cross-site requests doesn't apply; the token is what's needed."""
    language = _unsubscribe_language(token)
    notify_email.unsubscribe(token)

    def t(key):
        return escape(mailer.text(language, f"email.unsubscribe.{key}"))

    back = f'<p><a href="/?lang={language}#/inbox">{t("back")}</a></p>'
    return _unsubscribe_page(language, t("doneTitle"), t("done"), back)
