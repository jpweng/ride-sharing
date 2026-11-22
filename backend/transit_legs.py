"""
Public transport to the car and from it, for a search's matches
(routes/match.py): a drive rarely passes at the passenger's door.

For each of the best few matches:
  - "first_mile_transit": from the passenger's start to the pickup, when the
    pickup is too far to walk, arriving in time for the car;
  - "last_mile_transit": from the drop-off to their destination, likewise,
    leaving once the car is there.
Each is {from_station, to_station, connections} or None.

A leg is only proposed when it makes sense:
  - the gap is at least MIN_GAP_KM: nobody takes a bus for 1 or 2 km, they
    walk, and the driver may well go that bit further;
  - there is a stop within MAX_STOP_KM of where the drive passes (a short
    walk, or a small detour the two agree on), and it isn't the stop the
    passenger would start from (or end at) anyway;
  - a connection fits the car's time: it gets to the pickup's stop
    TRANSFER_MIN before the car, or leaves the drop-off's stop TRANSFER_MIN
    after it; and the leg, waiting included, takes at most MAX_LEG_MIN.

Looking these up costs a few calls per match to transport.opendata.ch, a
free, rate-limited API (answers are cached, see transit.py): only the first
MAX_MATCHES matches that need a leg get one, those that can be booked first.
"""
from datetime import datetime, timedelta

import transit

MIN_GAP_KM = 3.0
MAX_STOP_KM = 3.0
MAX_LEG_MIN = 120
MAX_MATCHES = 5

# Between a stop and the car: getting from one to the other, and finding
# each other.
TRANSFER_MIN = 10

# The timetable answers "be there by" with the last few connections before
# that time: the latest ones are the ones to show.
FIRST_MILE_SHOWN = 2


class _Lazy:
    """A station looked up at most once, and only if a match needs it."""

    def __init__(self, point):
        self.point = point
        self.looked_up = False
        self.station = None

    def get(self) -> dict | None:
        if not self.looked_up:
            self.looked_up = True
            place = self.point.name if self.point.kind == "place" else None
            poi = self.point.name if self.point.kind == "poi" else None
            self.station = transit.resolve_station(self.point.lat, self.point.lon, place, poi)
        return self.station


def _naive(iso: str) -> datetime | None:
    """A time as the clock shows it in Switzerland: the timetable's times
    carry an offset, the drives' don't."""
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _stop_near(meeting: dict) -> dict | None:
    """The stop nearest a pickup or drop-off point, or None when there is
    none near enough."""
    stop = transit.nearest_named_station(meeting["lat"], meeting["lon"])
    if not stop or not stop.get("name"):
        return None
    if (stop.get("distance_m") or 0.0) / 1000.0 > MAX_STOP_KM:
        return None
    return stop


def _leg(from_station: dict, to_station: dict, connections: list[dict]) -> dict | None:
    if not connections:
        return None
    return {"from_station": from_station["name"], "to_station": to_station["name"], "connections": connections}


def first_mile(match: dict, origin_station: _Lazy) -> dict | None:
    """Public transport from the passenger's start to the pickup."""
    pickup = match["pickup"]
    if pickup["gap_km"] < MIN_GAP_KM:
        return None
    stop = _stop_near(pickup)
    start = origin_station.get() if stop else None
    if not stop or not start or start["name"] == stop["name"]:
        return None
    be_there_by = _naive(pickup["time"]) - timedelta(minutes=TRANSFER_MIN)
    connections = transit.get_connections(
        start["name"], stop["name"], be_there_by.isoformat(timespec="minutes"), arrive_by=True,
    )
    fitting = []
    for c in connections:
        departure, arrival = _naive(c.get("departure")), _naive(c.get("arrival"))
        if departure and arrival and arrival <= be_there_by and departure >= be_there_by - timedelta(minutes=MAX_LEG_MIN):
            fitting.append(c)
    # The ones that leave the least waiting for the car.
    return _leg(start, stop, fitting[-FIRST_MILE_SHOWN:])


def last_mile(match: dict, destination_station: _Lazy) -> dict | None:
    """Public transport from the drop-off to the passenger's destination."""
    dropoff = match["dropoff"]
    if dropoff["gap_km"] < MIN_GAP_KM:
        return None
    stop = _stop_near(dropoff)
    end = destination_station.get() if stop else None
    if not stop or not end or end["name"] == stop["name"]:
        return None
    leave_from = _naive(dropoff["time"]) + timedelta(minutes=TRANSFER_MIN)
    connections = transit.get_connections(stop["name"], end["name"], leave_from.isoformat(timespec="minutes"))
    fitting = []
    for c in connections:
        departure, arrival = _naive(c.get("departure")), _naive(c.get("arrival"))
        if departure and arrival and departure >= leave_from and arrival <= leave_from + timedelta(minutes=MAX_LEG_MIN):
            fitting.append(c)
    return _leg(stop, end, fitting)


def add_to(matches: list[dict], origin, destination) -> None:
    """Gives each match its "first_mile_transit" and "last_mile_transit".
    `origin` and `destination` are the search's own (api.LatLon)."""
    origin_station, destination_station = _Lazy(origin), _Lazy(destination)
    for m in matches:
        m["first_mile_transit"] = None
        m["last_mile_transit"] = None
    in_need = [
        m for m in matches
        if m["pickup"]["gap_km"] >= MIN_GAP_KM or m["dropoff"]["gap_km"] >= MIN_GAP_KM
    ]
    # Those that can be booked first; equally bookable, in the list's order.
    in_need.sort(key=lambda m: not m.get("bookable", True))
    for m in in_need[:MAX_MATCHES]:
        m["first_mile_transit"] = first_mile(m, origin_station)
        m["last_mile_transit"] = last_mile(m, destination_station)
