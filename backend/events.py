"""
Events as ride destinations: Christmas markets, festivals, matches... places
thousands of people go to at the same time, the easiest rides to match.

The events are data, in events.json next to this file (see the README, "Adding
an event"): adding one is a pull request, there is no admin UI yet. The file
is validated when this module is imported, so the app doesn't start with a
broken one, and by the tests.

Each event has a slug (its link: /e/<slug> redirects to #/event/<slug>), a
name in each language, the place (a sensible drop-off point and the nearest
station), its first and last day, opening hours per weekday, and exceptions
for given dates (other hours, or null: closed). Hours are "HH:MM", Swiss local
time; a closing time at or before the opening time is the next day's.
`close` is when the market's stalls close; the optional `late_close` when
the last of it (restaurants, bars) does. Return trips are suggested from
`close` until RETURN_WINDOW after it, and at `late_close`.
"""
import json
import re
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

EVENTS_PATH = Path(__file__).with_name("events.json")

LANGS = ("de", "fr", "it", "en")
DEFAULT_LANG = "en"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Swiss local time, like drives' departure times.
LOCAL_TZ = ZoneInfo("Europe/Zurich")

# Departures suggested for the way back: from closing time until this long
# after it, every RETURN_STEP.
RETURN_WINDOW = timedelta(minutes=90)
RETURN_STEP = timedelta(minutes=30)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
# Switzerland and a margin around it (lat, lon).
BOUNDS = ((45.7, 48.0), (5.8, 10.6))

REQUIRED_KEYS = {"slug", "name", "place", "first_day", "last_day", "hours", "verified"}
OPTIONAL_KEYS = {"exceptions", "url", "notes", "todo"}
HOURS_KEYS = {"open", "close", "late_close"}


class EventDataError(ValueError):
    """events.json is invalid; the message lists every problem found."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _parse_date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def _check_hours(where: str, hours, problems: list, allow_verified: bool = False) -> None:
    if hours is None:
        return  # closed
    found = len(problems)
    if not isinstance(hours, dict):
        problems.append(f"{where}: must be an object with open/close, or null for closed")
        return
    allowed = HOURS_KEYS | ({"verified"} if allow_verified else set())
    for key in set(hours) - allowed:
        problems.append(f"{where}: unknown key {key!r}")
    for key in ("open", "close"):
        if key not in hours:
            problems.append(f"{where}: missing {key!r}")
    for key in HOURS_KEYS & set(hours):
        if not isinstance(hours[key], str) or not TIME_RE.match(hours[key]):
            problems.append(f"{where}.{key}: {hours[key]!r} is not a HH:MM time")
    if "verified" in hours and not isinstance(hours["verified"], bool):
        problems.append(f"{where}.verified: must be true or false")
    if len(problems) > found:
        return
    opens, closes = _span(date(2026, 1, 1), hours)
    if opens == closes:
        problems.append(f"{where}: opens and closes at the same time")
    if "late_close" in hours and _late(date(2026, 1, 1), hours) < closes:
        problems.append(f"{where}.late_close: before the closing time")


def validate(data) -> list[dict]:
    """The events in `data` (events.json's content); raises EventDataError
    listing every problem."""
    problems = []
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        raise EventDataError('events.json: expected {"events": [...]}')
    slugs = set()
    for i, event in enumerate(events):
        if not isinstance(event, dict):
            problems.append(f"events[{i}]: must be an object")
            continue
        where = f"events[{i}] ({event.get('slug', '?')})"
        for key in REQUIRED_KEYS - set(event):
            problems.append(f"{where}: missing {key!r}")
        for key in set(event) - REQUIRED_KEYS - OPTIONAL_KEYS:
            problems.append(f"{where}: unknown key {key!r}")

        slug = event.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.match(slug) or len(slug) > 60:
            problems.append(f"{where}.slug: lowercase letters, digits and hyphens only (at most 60)")
        elif slug in slugs:
            problems.append(f"{where}.slug: used by another event")
        slugs.add(slug)

        name = event.get("name")
        if not isinstance(name, dict) or set(name) != set(LANGS) or not all(
            isinstance(v, str) and v.strip() for v in name.values()
        ):
            problems.append(f"{where}.name: needs a non-empty name in each of {', '.join(LANGS)}, and nothing else")

        place = event.get("place")
        if not isinstance(place, dict):
            problems.append(f"{where}.place: must be an object")
        else:
            for key in set(place) - {"name", "lat", "lon", "station"}:
                problems.append(f"{where}.place: unknown key {key!r}")
            for key in ("name", "station"):
                if not isinstance(place.get(key), str) or not place[key].strip():
                    problems.append(f"{where}.place.{key}: must be a non-empty string")
            for key, (low, high) in zip(("lat", "lon"), BOUNDS):
                value = place.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                    problems.append(f"{where}.place.{key}: must be a number between {low} and {high}")

        first, last = _parse_date(event.get("first_day")), _parse_date(event.get("last_day"))
        if not first:
            problems.append(f"{where}.first_day: must be a YYYY-MM-DD date")
        if not last:
            problems.append(f"{where}.last_day: must be a YYYY-MM-DD date")
        if first and last and last < first:
            problems.append(f"{where}: last_day is before first_day")

        hours = event.get("hours")
        if not isinstance(hours, dict) or set(hours) != set(WEEKDAYS):
            problems.append(f"{where}.hours: needs each of {', '.join(WEEKDAYS)} (null when closed), and nothing else")
        else:
            for day in WEEKDAYS:
                _check_hours(f"{where}.hours.{day}", hours[day], problems)

        exceptions = event.get("exceptions", {})
        if not isinstance(exceptions, dict):
            problems.append(f"{where}.exceptions: must be an object of date -> hours or null")
        else:
            for key, value in exceptions.items():
                day = _parse_date(key)
                if not day:
                    problems.append(f"{where}.exceptions: {key!r} is not a YYYY-MM-DD date")
                elif first and last and not first <= day <= last:
                    problems.append(f"{where}.exceptions: {key} is outside the event's days")
                _check_hours(f"{where}.exceptions.{key}", value, problems, allow_verified=True)

        url = event.get("url")
        if url is not None and (not isinstance(url, str) or not url.startswith("https://")):
            problems.append(f"{where}.url: must be an https:// link")
        for key in ("notes", "todo"):
            if key in event and (not isinstance(event[key], str) or not event[key].strip()):
                problems.append(f"{where}.{key}: must be a non-empty string")

        verified = event.get("verified")
        if not isinstance(verified, bool):
            problems.append(f"{where}.verified: must be true or false")
        unverified_days = isinstance(exceptions, dict) and any(
            isinstance(v, dict) and v.get("verified") is False for v in exceptions.values()
        )
        if (verified is False or unverified_days) and "todo" not in event:
            problems.append(f"{where}: unverified data needs a 'todo' saying what to check")

        if not problems and not schedule(event):
            problems.append(f"{where}: never open")

    if problems:
        raise EventDataError("events.json is invalid:\n  " + "\n  ".join(problems))
    return events


def load(path: Path = EVENTS_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise EventDataError(f"{path.name}: not valid JSON: {e}") from e
    return validate(data)


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------

def _at(day: date, hhmm: str, after: datetime | None = None) -> datetime:
    """`hhmm` on `day`, or on the next day if that's not after `after`."""
    dt = datetime.combine(day, time.fromisoformat(hhmm))
    return dt + timedelta(days=1) if after is not None and dt <= after else dt


def _span(day: date, hours: dict) -> tuple[datetime, datetime]:
    opens = _at(day, hours["open"])
    return opens, _at(day, hours["close"], after=opens)


def _late(day: date, hours: dict) -> datetime:
    opens = _at(day, hours["open"])
    return _at(day, hours["late_close"], after=opens)


def hours_on(event: dict, day: date) -> dict | None:
    """The event's hours on `day` as datetimes ({open, close, late_close}),
    or None when it's closed or not on."""
    if not date.fromisoformat(event["first_day"]) <= day <= date.fromisoformat(event["last_day"]):
        return None
    exceptions = event.get("exceptions", {})
    hours = exceptions[day.isoformat()] if day.isoformat() in exceptions else event["hours"][WEEKDAYS[day.weekday()]]
    if hours is None:
        return None
    opens, closes = _span(day, hours)
    return {
        "open": opens,
        "close": closes,
        "late_close": _late(day, hours) if "late_close" in hours else None,
        "verified": event["verified"] and hours.get("verified", True),
    }


def return_times(hours: dict) -> list[datetime]:
    """Departures suggested for the way back: from closing time until
    RETURN_WINDOW after it, and when the restaurants and bars close."""
    times = []
    at = hours["close"]
    while at <= hours["close"] + RETURN_WINDOW:
        times.append(at)
        at += RETURN_STEP
    if hours["late_close"] and hours["late_close"] not in times:
        times.append(hours["late_close"])
    return sorted(times)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


def schedule(event: dict) -> list[dict]:
    """One entry per day the event is open, with its hours and the suggested
    departures back."""
    days = []
    day = date.fromisoformat(event["first_day"])
    last = date.fromisoformat(event["last_day"])
    while day <= last:
        hours = hours_on(event, day)
        if hours:
            days.append({
                "date": day.isoformat(),
                "open": _iso(hours["open"]),
                "close": _iso(hours["close"]),
                "late_close": _iso(hours["late_close"]),
                "return_times": [_iso(t) for t in return_times(hours)],
                "verified": hours["verified"],
            })
        day += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# For the API
# ---------------------------------------------------------------------------

EVENTS = load()
BY_SLUG = {e["slug"]: e for e in EVENTS}


def today() -> date:
    return datetime.now(LOCAL_TZ).date()


def name(event: dict, lang: str) -> str:
    return event["name"].get(lang) or event["name"][DEFAULT_LANG]


def summary(event: dict, lang: str, on: date) -> dict:
    first, last = date.fromisoformat(event["first_day"]), date.fromisoformat(event["last_day"])
    return {
        "slug": event["slug"],
        "name": name(event, lang),
        "names": event["name"],
        "place": event["place"],
        "first_day": event["first_day"],
        "last_day": event["last_day"],
        "status": "past" if last < on else "current" if first <= on else "upcoming",
        "url": event.get("url"),
        "verified": event["verified"],
    }


def upcoming(lang: str = DEFAULT_LANG, on: date | None = None) -> list[dict]:
    """Current and upcoming events, the soonest first."""
    on = on or today()
    found = [e for e in EVENTS if date.fromisoformat(e["last_day"]) >= on]
    return [summary(e, lang, on) for e in sorted(found, key=lambda e: (e["first_day"], e["slug"]))]


def detail(event: dict, lang: str = DEFAULT_LANG, on: date | None = None) -> dict:
    return {
        **summary(event, lang, on or today()),
        "hours": event["hours"],
        "exceptions": event.get("exceptions", {}),
        "schedule": schedule(event),
        "todo": event.get("todo"),
    }


# ---------------------------------------------------------------------------
# The shareable link, /e/<slug>
# ---------------------------------------------------------------------------

# The link preview's text (messaging apps show it under the title).
SHARE_DESCRIPTION = {
    "de": "Mitfahrgelegenheiten zu {name} und zurück, {dates}. Ride and Rail: geteilte Fahrten zum Zug.",
    "fr": "Covoiturage pour {name}, aller et retour, {dates}. Ride and Rail : trajets partagés vers le train.",
    "it": "Passaggi in auto per {name}, andata e ritorno, {dates}. Ride and Rail: passaggi condivisi verso il treno.",
    "en": "Shared rides to {name} and back, {dates}. Ride and Rail: shared rides to the train.",
}


def swiss_dates(first: str, last: str) -> str:
    """"20.11.–24.12.2026", the Swiss way, in every language."""
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    start = f"{a.day}.{a.month}." if a.year == b.year else f"{a.day}.{a.month}.{a.year}"
    return f"{start}–{b.day}.{b.month}.{b.year}"


def pick_lang(lang: str | None, accept_language: str | None) -> str:
    if lang in LANGS:
        return lang
    for part in (accept_language or "").split(","):
        base = part.split(";")[0].strip()[:2].lower()
        if base in LANGS:
            return base
    return DEFAULT_LANG


def share_page(slug: str, lang: str | None = None, accept_language: str | None = None) -> str:
    """A tiny page for /e/<slug>: a title and description for link previews,
    then straight on to the app's event page, #/event/<slug>. A ?lang= in
    the link (on a printed QR code, say) is passed on to the app."""
    shown_lang = pick_lang(lang, accept_language)
    target = ("/?lang=" + lang if lang in LANGS else "/") + "#/event/" + quote(slug, safe="")
    event = BY_SLUG.get(slug)
    title, description = "Ride and Rail", ""
    if event:
        title = f"{name(event, shown_lang)} — Ride and Rail"
        description = SHARE_DESCRIPTION[shown_lang].format(
            name=name(event, shown_lang), dates=swiss_dates(event["first_day"], event["last_day"])
        )
    return f"""<!DOCTYPE html>
<html lang="{shown_lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="robots" content="noindex, nofollow" />
<title>{escape(title)}</title>
<meta property="og:title" content="{escape(title)}" />
<meta property="og:description" content="{escape(description)}" />
<meta property="og:type" content="website" />
<meta name="description" content="{escape(description)}" />
<meta http-equiv="refresh" content="0; url={escape(target)}" />
<script>location.replace({json.dumps(target)});</script>
</head>
<body>
<p><a href="{escape(target)}">{escape(title)}</a></p>
</body>
</html>
"""
