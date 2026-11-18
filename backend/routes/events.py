"""
Events as ride destinations (see events.py, events.json):

  GET /api/events          -- current and upcoming events.
  GET /api/events/{slug}   -- one event with its hours and schedule.
  GET /e/{slug}            -- an event's shareable link: a small page with
                              a link preview, redirecting to #/event/{slug}.
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Header
from fastapi.responses import HTMLResponse

import events
from api import api_error

router = APIRouter()

EventLang = Literal["de", "fr", "it", "en"]


@router.get("/api/events")
def list_events(lang: EventLang = events.DEFAULT_LANG):
    return events.upcoming(lang)


@router.get("/api/events/{slug}")
def get_event(slug: str, lang: EventLang = events.DEFAULT_LANG):
    event = events.BY_SLUG.get(slug)
    if not event:
        raise api_error(404, "event_not_found", "Event not found")
    return events.detail(event, lang)


@router.get("/e/{slug}", response_class=HTMLResponse, include_in_schema=False)
def event_link(slug: str, lang: str | None = None, accept_language: Annotated[str | None, Header()] = None):
    # The link people share (WhatsApp, QR codes): short, no "#", and with a
    # preview (title and dates) for the apps that show one.
    page = events.share_page(slug, lang, accept_language)
    return HTMLResponse(page, status_code=200 if slug in events.BY_SLUG else 404)
