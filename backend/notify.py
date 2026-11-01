"""
Ride requests: a passenger asks for a ride nobody has posted, and a few
drivers who made a suitable drive in the past are told. When a drive that
fits an open request is posted, its passenger is told. The design, and the
reasons behind every limit below, are in docs/ride-predictions.md.

Who is told about a request (notify_drivers):
  - drivers with a drive in the last HISTORY_WEEKS weeks whose route passes
    within the request's pickup and drop-off radii (matching.match_drive),
    with a pickup within HISTORY_TIME_TOLERANCE_MIN of the requested time
    of day;
  - except the passenger, drivers who already posted a matching drive (the
    passenger sees it already), drivers who muted the route or turned
    request notifications off, and drivers over their budget;
  - visitors' drivers before demo drivers, then the most frequent first.

Demo drivers' history isn't stored: it follows from their templates in
demo_drives.py (each one driven on its weekdays, minus DEMO_SKIP_RATE).

Notifications are stored first (the notifications table), then handed to
each channel in CHANNELS. The only channel today is the in-app inbox, which
shows the stored rows; email will plug in there with the same limits.
"""
import random
from datetime import datetime, timedelta, timezone

import database as db
import demo_drives
import matching
import search

LOCAL_TZ = demo_drives.LOCAL_TZ

REQUEST_MIN_AHEAD = timedelta(hours=1)
REQUEST_MAX_AHEAD = timedelta(days=14)

HISTORY_WEEKS = 13
HISTORY_TIME_TOLERANCE_MIN = 60

DRIVERS_PER_REQUEST = 3
EXTRA_ROUND_DRIVERS = 2
EXTRA_ROUND_AFTER = timedelta(hours=12)  # without an offer, and the trip still this far away

MAX_PER_DAY = 1
MAX_PER_WEEK = 3
BACKOFF_SLOW_AFTER = 3  # unanswered notifications in a row: down to 1 a week
BACKOFF_PAUSE_AFTER = 5  # ...then a pause
BACKOFF_PAUSE = timedelta(days=30)

# Ranking: occurrences of the trip in the history, this many more if driven
# on the request's weekday, minus the match's score (minutes lost, see
# matching.py) divided by RANK_MINUTES_PER_POINT.
SAME_WEEKDAY_BONUS = 4
RANK_MINUTES_PER_POINT = 10

SAME_ROUTE_KM = 1.5  # two requests are "the same route" if both ends are this close
MUTE_RADIUS_KM = 5.0  # a muted route covers requests starting and ending this close to it

DEMO_SKIP_RATE = 0.1  # demo drivers skip one occurrence in ten


class InboxChannel:
    """The in-app inbox: it shows the stored notifications, nothing to send."""

    def deliver(self, notification: dict) -> None:
        pass


CHANNELS = [InboxChannel()]


def _send(recipient_name: str, kind: str, request_id: int, **fields) -> dict:
    notification = db.create_notification(recipient_name, kind, request_id, **fields)
    for channel in CHANNELS:
        channel.deliver(notification)
    return notification


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _round_to_5_min(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % 5) + (timedelta(minutes=5) if dt.minute % 5 >= 3 else timedelta())


# ---------------------------------------------------------------------
# Drivers' history
# ---------------------------------------------------------------------
_demo_geometries: dict[tuple[str, str], list | None] = {}


def _demo_geometry(key: tuple[str, str]) -> list | None:
    if key not in _demo_geometries:
        route = db.find_demo_route(*key)
        _demo_geometries[key] = route["geometry"] if route else None
    return _demo_geometries[key]


def _history(now: datetime) -> list[dict]:
    """
    Past trips, as drive-like dicts: the driver, both ends, road distance and
    duration, the time of day ("time"), whether it's a demo driver, and a
    "dates" function giving the days it was driven (computed only for trips
    that pass the cheap tests: a demo template is up to 65 days).
    Route geometry is loaded later, for the few trips that need it.
    """
    since = now - timedelta(weeks=HISTORY_WEEKS)
    trips = []
    for d in db.past_visitor_drives(_iso(since), _iso(now)):
        departure = datetime.fromisoformat(d["departure_time"]).replace(tzinfo=None)
        trips.append({
            **d,
            "time": departure.time(),
            "dates": lambda day=departure.date(): [day],
            "load_geometry": lambda geometry=d.get("route_geometry"): geometry,
        })

    routes = db.all_demo_routes()
    for spec in demo_drives.DEMO_DRIVES:
        key = (spec["origin"]["name"], spec["destination"]["name"])
        if key not in routes:
            continue

        def dates(spec=spec):
            days = []
            day = since.date()
            while day < now.date():
                if day.weekday() in spec["weekdays"]:
                    rng = random.Random(f"history {spec['driver_name']} {day} {spec['time']}")
                    if rng.random() >= DEMO_SKIP_RATE:
                        days.append(day)
                day += timedelta(days=1)
            return days

        trips.append({
            "driver_name": spec["driver_name"],
            "origin_name": spec["origin"]["name"],
            "origin_lat": spec["origin"]["lat"],
            "origin_lon": spec["origin"]["lon"],
            "destination_name": spec["destination"]["name"],
            "destination_lat": spec["destination"]["lat"],
            "destination_lon": spec["destination"]["lon"],
            "route_distance_m": routes[key]["distance_m"],
            "route_duration_s": routes[key]["duration_s"],
            "is_demo": 1,
            "time": spec["time"],
            "dates": dates,
            "load_geometry": lambda key=key: _demo_geometry(key),
        })
    return trips


def _candidates(request: dict, now: datetime) -> list[dict]:
    """
    Drivers whose past trips fit the request, best first:
    [{driver_name, is_demo, times, same_weekday, score, trip, match}].
    """
    asked = datetime.fromisoformat(request["requested_time"])
    origin = (request["origin_lat"], request["origin_lon"])
    destination = (request["destination_lat"], request["destination_lon"])
    window = timedelta(minutes=HISTORY_TIME_TOLERANCE_MIN)

    by_driver = {}
    for trip in _history(now):
        departure = datetime.combine(asked.date(), trip["time"])
        duration = timedelta(seconds=trip.get("route_duration_s") or 0)
        # The pickup is somewhere between the departure and the arrival.
        if not (asked - window - duration <= departure <= asked + window):
            continue
        if not (
            matching.could_pass_near(trip, origin, request["pickup_radius_km"])
            and matching.could_pass_near(trip, destination, request["dropoff_radius_km"])
        ):
            continue
        drive = {**trip, "departure_time": _iso(departure), "seats_available": 1,
                 "route_geometry": trip["load_geometry"]()}
        m = matching.match_drive(
            drive, origin, destination, request["requested_time"],
            request["pickup_radius_km"], request["dropoff_radius_km"], HISTORY_TIME_TOLERANCE_MIN,
        )
        if m is None:
            continue
        days = trip["dates"]()
        if not days:
            continue
        c = by_driver.setdefault(trip["driver_name"], {
            "driver_name": trip["driver_name"], "is_demo": bool(trip.get("is_demo")),
            "times": 0, "same_weekday": False, "trip": None, "match": None,
        })
        c["times"] += len(days)
        c["same_weekday"] |= any(day.weekday() == asked.weekday() for day in days)
        if c["match"] is None or m["score"] < c["match"]["score"]:
            c["trip"], c["match"] = trip, m

    for c in by_driver.values():
        c["score"] = (
            c["times"] + SAME_WEEKDAY_BONUS * c["same_weekday"] - c["match"]["score"] / RANK_MINUTES_PER_POINT
        )
    # Real drivers first: the demo network's thousands of fictional drivers,
    # with weeks of history each, would otherwise always take the places.
    return sorted(by_driver.values(), key=lambda c: (c["is_demo"], -c["score"]))


# ---------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------
def backoff(name: str, now_utc: datetime | None = None) -> dict:
    """
    A driver's request-notification budget: {"unanswered": n in a row,
    "paused_until": UTC datetime or None, "can_notify": bool}.
    Reading a notification isn't an answer; offering, declining or muting is.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    settings = db.get_user_settings(name)
    sent = db.list_notifications(name, kind="request_for_driver")  # newest first

    unanswered = 0
    for n in sent:
        if settings["backoff_reset_at"] and n["created_at"] < settings["backoff_reset_at"]:
            break
        if n["acted_at"]:
            break
        unanswered += 1

    paused_until = None
    if unanswered >= BACKOFF_PAUSE_AFTER:
        paused_until = _utc(sent[0]["created_at"]) + BACKOFF_PAUSE
        if paused_until <= now_utc:
            paused_until = None

    week = [n for n in sent if _utc(n["created_at"]) > now_utc - timedelta(days=7)]
    today = now_utc.astimezone(LOCAL_TZ).date()
    today_count = sum(1 for n in week if _utc(n["created_at"]).astimezone(LOCAL_TZ).date() == today)
    week_limit = 1 if unanswered >= BACKOFF_SLOW_AFTER else MAX_PER_WEEK
    can_notify = (
        settings["notify_requests"]
        and paused_until is None
        and today_count < MAX_PER_DAY
        and len(week) < week_limit
    )
    return {"unanswered": unanswered, "paused_until": paused_until, "can_notify": can_notify}


def _same_route(a: dict, b: dict, km: float) -> bool:
    return (
        matching.haversine_km(a["origin_lat"], a["origin_lon"], b["origin_lat"], b["origin_lon"]) <= km
        and matching.haversine_km(a["destination_lat"], a["destination_lon"],
                                  b["destination_lat"], b["destination_lon"]) <= km
    )


def _muted(driver_name: str, request: dict) -> bool:
    return any(_same_route(mute, request, MUTE_RADIUS_KM) for mute in db.list_route_mutes(driver_name))


def _unread_same_trip(driver_name: str, request: dict) -> dict | None:
    """An unread notification to this driver about a request on the same
    route and day: the new request is merged into it."""
    for n in db.list_notifications(driver_name, kind="request_for_driver"):
        if n["read_at"]:
            continue
        other = db.get_open_request(n["request_id"])
        if (
            other
            and other["requested_time"][:10] == request["requested_time"][:10]
            and _same_route(other, request, SAME_ROUTE_KM)
        ):
            return n
    return None


# ---------------------------------------------------------------------
# Telling drivers about a request, passengers about a drive
# ---------------------------------------------------------------------
def notify_drivers(request: dict, limit: int = DRIVERS_PER_REQUEST, exclude: set[str] = frozenset(),
                   now: datetime | None = None) -> list[str]:
    """Tell up to `limit` suitable drivers about `request`; returns their names."""
    now = now or local_now()
    origin = (request["origin_lat"], request["origin_lon"])
    destination = (request["destination_lat"], request["destination_lon"])
    already_posted = {
        m["driver_name"] for m in search.upcoming_matches(
            origin, destination, request["requested_time"],
            request["pickup_radius_km"], request["dropoff_radius_km"], request["time_tolerance_min"],
            max_results=10**6,
        )
    }

    told = []
    for c in _candidates(request, now):
        if len(told) >= limit:
            break
        name = c["driver_name"]
        if name == request["passenger_name"] or name in exclude or name in already_posted:
            continue
        if not db.get_user_settings(name)["notify_requests"] or _muted(name, request):
            continue
        same_trip = _unread_same_trip(name, request)
        if same_trip:
            # "2 passengers…": better news, not more noise, so not counted
            # against the budget.
            db.merge_into_notification(same_trip["id"], request["id"])
            told.append(name)
            continue
        if not backoff(name)["can_notify"]:
            continue  # the next candidate is tried; nothing waits in a queue

        trip, m = c["trip"], c["match"]
        # Leave so as to reach the pickup at the requested time.
        lead = datetime.fromisoformat(m["pickup"]["time"]) - datetime.fromisoformat(m["departure_time"])
        departure = _round_to_5_min(datetime.fromisoformat(request["requested_time"]) - lead)
        _send(name, "request_for_driver", request["id"], details={
            "trip": {
                "origin": {"name": trip["origin_name"], "lat": trip["origin_lat"], "lon": trip["origin_lon"]},
                "destination": {"name": trip["destination_name"], "lat": trip["destination_lat"],
                                "lon": trip["destination_lon"]},
                "time": trip["time"].strftime("%H:%M"),
                "times": c["times"],
            },
            "suggested_departure": _iso(departure),
            "pickup": m["pickup"],
            "dropoff": m["dropoff"],
        })
        told.append(name)
    return told


def tell_passengers(drive: dict, now: datetime | None = None) -> int:
    """A drive was posted: tell the passengers of the open requests it fits.
    At most one notification per request per day; more drives are merged
    into it. Returns how many requests it fits."""
    now = now or local_now()
    if drive.get("route_geometry") is None and drive.get("id") is not None:
        drive = db.get_drive(drive["id"]) or drive
    today = datetime.now(timezone.utc).astimezone(LOCAL_TZ).date()
    fitted = 0
    for r in db.list_open_requests(_iso(now)):
        if r["passenger_name"] == drive["driver_name"]:
            continue
        m = matching.match_drive(
            drive, (r["origin_lat"], r["origin_lon"]), (r["destination_lat"], r["destination_lon"]),
            r["requested_time"], r["pickup_radius_km"], r["dropoff_radius_km"], r["time_tolerance_min"],
        )
        if m is None:
            continue
        fitted += 1
        sent_today = [
            n for n in db.notified_for_request(r["id"], "drive_for_passenger")
            if _utc(n["created_at"]).astimezone(LOCAL_TZ).date() == today
        ]
        if sent_today:
            db.merge_into_notification(sent_today[0]["id"], r["id"], drive_id=drive["id"])
        else:
            _send(r["passenger_name"], "drive_for_passenger", r["id"], drive_ids=[drive["id"]])
    return fitted


def run_extra_rounds(now: datetime | None = None) -> int:
    """Once per request: if no fitting drive was posted EXTRA_ROUND_AFTER after
    it was made and the trip is still that far away, tell up to
    EXTRA_ROUND_DRIVERS more drivers. Returns how many drivers were told."""
    now = now or local_now()
    now_utc = now.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    told = 0
    for r in db.list_open_requests(_iso(now + EXTRA_ROUND_AFTER)):
        if r["extra_round_done"] or now_utc - _utc(r["created_at"]) < EXTRA_ROUND_AFTER:
            continue
        if not db.notified_for_request(r["id"], "drive_for_passenger"):
            already = {n["recipient_name"] for n in db.notified_for_request(r["id"], "request_for_driver")}
            told += len(notify_drivers(r, EXTRA_ROUND_DRIVERS, exclude=already, now=now))
        db.mark_extra_round_done(r["id"])
    return told


# ---------------------------------------------------------------------
# The inbox
# ---------------------------------------------------------------------
def _request_summary(request_id: int) -> dict | None:
    r = db.get_open_request(request_id)
    if r is None:
        return None
    return {k: r[k] for k in (
        "id", "passenger_name", "origin_name", "origin_lat", "origin_lon", "destination_name",
        "destination_lat", "destination_lon", "requested_time", "pickup_radius_km", "dropoff_radius_km",
        "time_tolerance_min", "status",
    )}


def inbox(name: str) -> dict:
    """A user's notifications, newest first, with what they're about, and
    their request-notification settings."""
    items = []
    for n in db.list_notifications(name):
        item = {k: n[k] for k in ("id", "kind", "created_at", "read_at", "acted_at", "action", "details")}
        item["request"] = _request_summary(n["request_id"])
        item["passengers"] = len(n["request_ids"])
        if n["kind"] == "drive_for_passenger":
            drives = [db.get_drive(i) for i in n["drive_ids"] or []]
            item["drives"] = [
                {k: d[k] for k in ("id", "driver_name", "origin_name", "destination_name", "departure_time",
                                   "seats_available", "is_demo")}
                for d in drives if d
            ]
        items.append(item)
    state = backoff(name)
    return {
        "notifications": items,
        "unread": sum(1 for i in items if not i["read_at"]),
        "notify_requests": db.get_user_settings(name)["notify_requests"],
        "paused_until": state["paused_until"].isoformat() if state["paused_until"] else None,
    }
