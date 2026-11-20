"""
Ride requests: a passenger asks for a ride nobody has posted, and a few
drivers are told: those who made a suitable drive in the past, and those
with an area (areas.py) the request starts or ends in. When a drive that
fits an open request is posted, its passenger is told. The design, and the
reasons behind every limit below, are in docs/ride-predictions.md.

Who is told about a request (notify_drivers):
  - drivers with a drive in the last HISTORY_WEEKS weeks whose route passes
    within the request's pickup and drop-off radii (matching.match_drive),
    with a pickup within HISTORY_TIME_TOLERANCE_MIN of the requested time
    of day, the most frequent first: up to DRIVERS_PER_REQUEST of them;
  - then drivers who saved an area the request starts or ends in
    (areas.drivers_near), whether or not they ever made the trip: up to
    AREA_DRIVERS_PER_REQUEST more, those with both ends in their areas
    first, then the nearest to their area's centre;
  - except the passenger, drivers who already posted a matching drive (the
    passenger sees it already), drivers who muted the route or turned
    request notifications off, and drivers over their budget. The budget
    is one per driver, whichever way they were chosen.

Real and demo never mix (is_demo_request): a real passenger's request only
reaches drivers of real (visitors') drives, so nobody waits for a driver
who doesn't exist, and the "drivers told" count is of real drivers only. A
request made under a demo driver's name (someone viewing the demo as, e.g.,
Jean Dupont) is a demo request: it only reaches demo drivers, so the demo
still shows requests arriving, and no real driver acts on a fictional need.
With demo drives off (demo_drives.enabled), there are no demo requests and
no demo drivers. Demo drivers have no areas: a demo request reaches nobody
by area.

Demo drivers' history isn't stored: it follows from their templates in
demo_drives.py (each one driven on its weekdays, minus DEMO_SKIP_RATE).

Notifications are stored first (the notifications table), then handed to
each channel in CHANNELS. The only channel today is the in-app inbox, which
shows the stored rows; email will plug in there with the same limits.

Kinds: request_for_driver, drive_for_passenger, and drive_cancelled (a drive
a passenger was told about was cancelled, see tell_drive_cancelled).
bookings.py sends its own through send(): they aren't about a ride request.
"""
import random
from datetime import datetime, timedelta, timezone

import areas
import database as db
import demo_drives
import matching
import search

LOCAL_TZ = demo_drives.LOCAL_TZ

REQUEST_MIN_AHEAD = timedelta(minutes=15)  # less, and no driver could make it
REQUEST_MAX_AHEAD = timedelta(days=14)

HISTORY_WEEKS = 13
HISTORY_TIME_TOLERANCE_MIN = 60

DRIVERS_PER_REQUEST = 3  # drivers who made the trip before
AREA_DRIVERS_PER_REQUEST = 3  # and as many with an area the request starts or ends in
EXTRA_ROUND_DRIVERS = 2  # of either kind
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


# The request id of notifications that aren't about a ride request.
NO_REQUEST = 0


def send(recipient_name: str, kind: str, details: dict, request_id: int = NO_REQUEST) -> dict:
    """A notification of bookings.py's, about seats on a drive: the inbox
    shows its kind and details. Most aren't about a ride request;
    `request_id` is for the one that is (a driver accepted it)."""
    return _send(recipient_name, kind, request_id, details=details)


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _round_to_5_min(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % 5) + (timedelta(minutes=5) if dt.minute % 5 >= 3 else timedelta())


def time_kind(request: dict) -> str:
    """What a request's time is: "depart" (when to leave) or "arrive" (when
    to be there), see matching.py."""
    return request.get("time_kind") or "depart"


def leave_by(origin, destination, arrive_by: datetime) -> datetime:
    """When a drive from `origin` to `destination` (each with .lat and .lon)
    has to start to be there by `arrive_by`: its road duration earlier (a
    straight-line estimate without the routing server), down to 5 minutes."""
    route = matching.route_with_fallback([(origin.lat, origin.lon), (destination.lat, destination.lon)])
    start = (arrive_by - timedelta(seconds=route["duration_s"])).replace(second=0, microsecond=0)
    return start - timedelta(minutes=start.minute % 5)


# ---------------------------------------------------------------------
# Drivers' history
# ---------------------------------------------------------------------
_demo_geometries: dict[tuple[str, str], list | None] = {}


def _demo_geometry(key: tuple[str, str]) -> list | None:
    if key not in _demo_geometries:
        route = db.find_demo_route(*key)
        _demo_geometries[key] = route["geometry"] if route else None
    return _demo_geometries[key]


def is_demo_request(request: dict) -> bool:
    """A request made under a demo driver's name: it only reaches demo
    drivers, and a real passenger's request only real ones."""
    return demo_drives.is_demo_user(request["passenger_name"])


def _history(now: datetime, demo: bool = False) -> list[dict]:
    """
    Past trips, as drive-like dicts: the driver, both ends, road distance and
    duration, the time of day ("time"), whether it's a demo driver, and a
    "dates" function giving the days it was driven (computed only for trips
    that pass the cheap tests: a demo template is up to 65 days).
    Route geometry is loaded later, for the few trips that need it.
    `demo`: the demo drivers' trips (none while demo drives are off);
    otherwise the visitors' drives, except those posted under a demo
    driver's name.
    """
    since = now - timedelta(weeks=HISTORY_WEEKS)
    trips = []
    if demo:
        if demo_drives.enabled():
            trips += _demo_history(since, now)
        return trips
    for d in db.past_visitor_drives(_iso(since), _iso(now)):
        if demo_drives.is_demo_user(d["driver_name"]):
            continue
        departure = datetime.fromisoformat(d["departure_time"]).replace(tzinfo=None)
        trips.append({
            **d,
            "time": departure.time(),
            "dates": lambda day=departure.date(): [day],
            "load_geometry": lambda geometry=d.get("route_geometry"): geometry,
        })
    return trips


def _demo_history(since: datetime, now: datetime) -> list[dict]:
    """The demo drivers' past trips, from their templates (see _history)."""
    trips = []
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
    Demo drivers for a demo request, real drivers otherwise, never both.
    """
    asked = datetime.fromisoformat(request["requested_time"])
    origin = (request["origin_lat"], request["origin_lon"])
    destination = (request["destination_lat"], request["destination_lon"])
    window = timedelta(minutes=HISTORY_TIME_TOLERANCE_MIN)

    by_driver = {}
    for trip in _history(now, demo=is_demo_request(request)):
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
            time_kind=time_kind(request),
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
    return sorted(by_driver.values(), key=lambda c: -c["score"])


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
def _area_candidates(request: dict) -> list[dict]:
    """Drivers with an area the request starts or ends in, best first
    (areas.drivers_near). Drivers who fit equally well (several who picked
    the same town as their area, say) take turns: the one told about a
    request longest ago comes first. None for a demo request: demo drivers
    have no areas, and no real driver acts on a fictional need."""
    if is_demo_request(request):
        return []
    with db.get_conn() as conn:
        last_told = dict(conn.execute(
            """
            SELECT recipient_name, MAX(created_at) FROM notifications
            WHERE kind = 'request_for_driver' GROUP BY recipient_name
            """
        ).fetchall())
    return sorted(
        areas.drivers_near(request),
        key=lambda d: (-len(d["ends"]), d["distance_km"], last_told.get(d["driver_name"], ""), d["driver_name"]),
    )


def _trip_details(candidate: dict, request: dict) -> dict:
    """What a driver who made the trip is shown: their usual trip, and a
    departure that reaches the pickup at the requested time (or, for a time
    to arrive by, the drop-off by then: never rounded to later)."""
    trip, m = candidate["trip"], candidate["match"]
    arrive = time_kind(request) == "arrive"
    lead = datetime.fromisoformat(m["dropoff" if arrive else "pickup"]["time"]) - datetime.fromisoformat(m["departure_time"])
    departure = datetime.fromisoformat(request["requested_time"]) - lead
    if arrive:
        departure = departure.replace(second=0, microsecond=0)
        departure -= timedelta(minutes=departure.minute % 5)
    else:
        departure = _round_to_5_min(departure)
    return {
        "trip": {
            "origin": {"name": trip["origin_name"], "lat": trip["origin_lat"], "lon": trip["origin_lon"]},
            "destination": {"name": trip["destination_name"], "lat": trip["destination_lat"],
                            "lon": trip["destination_lon"]},
            "time": trip["time"].strftime("%H:%M"),
            "times": candidate["times"],
        },
        "suggested_departure": _iso(departure),
        "pickup": m["pickup"],
        "dropoff": m["dropoff"],
    }


def _area_details(candidate: dict) -> dict:
    """What a driver chosen by area is shown: which of their areas, and
    whether the request starts or ends in it."""
    return {"area": candidate["area"], "ends": candidate["ends"]}


def notify_drivers(request: dict, limit: int = DRIVERS_PER_REQUEST, exclude: set[str] = frozenset(),
                   now: datetime | None = None, area_limit: int = AREA_DRIVERS_PER_REQUEST,
                   total: int | None = None) -> list[str]:
    """Tell suitable drivers about `request`: up to `limit` who made the trip
    before, then up to `area_limit` with an area it starts or ends in, and
    no more than `total` in all. Returns their names."""
    now = now or local_now()
    origin = (request["origin_lat"], request["origin_lon"])
    destination = (request["destination_lat"], request["destination_lon"])
    already_posted = {
        m["driver_name"] for m in search.upcoming_matches(
            origin, destination, request["requested_time"],
            request["pickup_radius_km"], request["dropoff_radius_km"], request["time_tolerance_min"],
            max_results=10**6, time_kind=time_kind(request),
        )
    }

    # Drivers who made the trip come first; one found both ways is told as
    # one who made the trip.
    candidates = [("trip", c) for c in _candidates(request, now)]
    made_the_trip = {c["driver_name"] for _, c in candidates}
    candidates += [("area", c) for c in _area_candidates(request) if c["driver_name"] not in made_the_trip]
    left = {"trip": limit, "area": area_limit}

    told = []
    for chosen_by, c in candidates:
        if total is not None and len(told) >= total:
            break
        if left[chosen_by] <= 0:
            continue
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
        elif not backoff(name)["can_notify"]:
            continue  # the next candidate is tried; nothing waits in a queue
        else:
            details = _trip_details(c, request) if chosen_by == "trip" else _area_details(c)
            _send(name, "request_for_driver", request["id"], details=details)
        told.append(name)
        left[chosen_by] -= 1
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
            time_kind=time_kind(r),
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


def tell_drive_cancelled(drive: dict, now: datetime | None = None, exclude: set[str] = frozenset()) -> int:
    """An upcoming drive is cancelled (by its driver, or because they deleted
    their account): tell the passengers who were told about it, except those
    in `exclude` (the ones with a seat on it, whom bookings.py tells). The
    drive's details are kept in the notification, as the drive itself is
    deleted. Returns how many passengers were told."""
    now = now or local_now()
    if datetime.fromisoformat(drive["departure_time"]).replace(tzinfo=None) <= now:
        return 0
    details = {"drive": {k: drive[k] for k in (
        "driver_name", "origin_name", "destination_name", "departure_time", "is_demo",
    )}}
    told = set()
    for n in db.notified_about_drive(drive["id"]):
        if n["recipient_name"] in told or n["recipient_name"] in exclude:
            continue
        _send(n["recipient_name"], "drive_cancelled", n["request_id"], details=details)
        told.add(n["recipient_name"])
    return len(told)


def run_extra_rounds(now: datetime | None = None) -> int:
    """Once per request: if no fitting drive was posted EXTRA_ROUND_AFTER after
    it was made and the trip is still that far away, tell up to
    EXTRA_ROUND_DRIVERS more drivers, of either kind. Returns how many
    drivers were told."""
    now = now or local_now()
    now_utc = now.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    told = 0
    for r in db.list_open_requests(_iso(now + EXTRA_ROUND_AFTER)):
        if r["extra_round_done"] or now_utc - _utc(r["created_at"]) < EXTRA_ROUND_AFTER:
            continue
        if not db.notified_for_request(r["id"], "drive_for_passenger"):
            already = {n["recipient_name"] for n in db.notified_for_request(r["id"], "request_for_driver")}
            told += len(notify_drivers(
                r, EXTRA_ROUND_DRIVERS, exclude=already, now=now,
                area_limit=EXTRA_ROUND_DRIVERS, total=EXTRA_ROUND_DRIVERS,
            ))
        db.mark_extra_round_done(r["id"])
    return told


# ---------------------------------------------------------------------
# The inbox
# ---------------------------------------------------------------------
_REQUEST_SHOWN = (
    "id", "passenger_id", "passenger_name", "origin_name", "origin_lat", "origin_lon", "destination_name",
    "destination_lat", "destination_lon", "requested_time", "pickup_radius_km", "dropoff_radius_km",
    "time_tolerance_min", "status", "time_kind", "leave_by",
)


def _request_summary(request_id: int) -> dict | None:
    r = db.get_open_request(request_id)
    if r is None:
        return None
    return {k: r[k] for k in _REQUEST_SHOWN}


# The most requests a driver's list shows, the soonest.
MAX_REQUESTS_LISTED = 200


def requests_in_areas(user: dict, now: datetime | None = None) -> list[dict]:
    """
    The open ride requests that start or end in one of the driver's areas
    (areas.py), the soonest first: what a notified driver sees of a request,
    with how it fits their areas ("area": the one to name, "ends": which
    ends their areas cover, "area_ids": every area of theirs it touches, to
    filter by). Every such request, not only those the driver
    was told about: the list has no budget, the driver comes to it.
    Not the driver's own requests, nor demo requests (fictional needs).
    """
    now = now or local_now()
    user_areas = areas.list_for(user["id"])
    if not user_areas:
        return []
    listed = []
    for r in db.list_open_requests(_iso(now)):
        if r["passenger_id"] == user["id"] or is_demo_request(r):
            continue
        fit = areas.best_fit(user_areas, r)
        if fit:
            listed.append({
                **{k: r[k] for k in _REQUEST_SHOWN},
                "area": fit["area"], "ends": fit["ends"], "area_ids": fit["area_ids"],
            })
        if len(listed) >= MAX_REQUESTS_LISTED:
            break
    return listed


def inbox(name: str, demo_view: bool = False) -> dict:
    """A user's notifications, newest first, with what they're about, and
    their request-notification settings. `demo_view` (someone viewing the
    demo as a demo driver, without signing in) leaves out notifications
    about real passengers' requests: their trips are nobody else's business."""
    items = []
    for n in db.list_notifications(name):
        request = db.get_open_request(n["request_id"])
        if demo_view and not (request and db.is_demo_user(request["passenger_id"])):
            continue
        item = {k: n[k] for k in ("id", "kind", "created_at", "read_at", "acted_at", "action", "details")}
        item["request"] = _request_summary(n["request_id"])
        item["passengers"] = len(n["request_ids"])
        if n["kind"] == "drive_for_passenger":
            drives = [db.get_drive(i) for i in n["drive_ids"] or []]
            item["drives"] = [
                search.with_bookable({k: d[k] for k in (
                    "id", "driver_name", "origin_name", "destination_name", "departure_time",
                    "seats_available", "is_demo",
                )})
                for d in drives if d and (demo_drives.enabled() or not d["is_demo"])
            ]
        items.append(item)
    state = backoff(name)
    return {
        "notifications": items,
        "unread": sum(1 for i in items if not i["read_at"]),
        "notify_requests": db.get_user_settings(name)["notify_requests"],
        "paused_until": state["paused_until"].isoformat() if state["paused_until"] else None,
    }
