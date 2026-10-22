"""
Keeps a rolling week of fictional drives in the database, so "Browse rides"
and matching always have something to show on the public demo.

Demo drives are flagged `is_demo` and are the only drives touched here:
drives posted by visitors are never modified or deleted. refresh() is
idempotent; main.py runs it at startup and then hourly. It drops demo drives
that have departed and adds the ones missing from the next DAYS_AHEAD days.

Run it by hand with the backend on PYTHONPATH, e.g.:
    PYTHONPATH=backend uv run python backend/demo_drives.py
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import database as db
import matching

DAYS_AHEAD = 7

# Departure times are naive Swiss local time, like the ones the frontend's
# datetime-local input sends.
LOCAL_TZ = ZoneInfo("Europe/Zurich")

MON_FRI = (0, 1, 2, 3, 4)

# Recurring drives, like commuters or weekend trips: one drive per listed
# weekday (Monday = 0) at the given local time.
DEMO_DRIVES = [
    {
        "driver_name": "Jean Dupont",
        "origin": {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323},
        "destination": {"name": "Genève", "lat": 46.2044, "lon": 6.1432},
        "time": time(7, 15),
        "weekdays": MON_FRI,
        "seats_available": 3,
    },
    {
        "driver_name": "Sophie Meier",
        "origin": {"name": "Zürich", "lat": 47.3769, "lon": 8.5417},
        "destination": {"name": "Bern", "lat": 46.9480, "lon": 7.4474},
        "time": time(7, 45),
        "weekdays": MON_FRI,
        "seats_available": 2,
    },
    {
        "driver_name": "Marco Rossi",
        "origin": {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619},
        "destination": {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323},
        "time": time(17, 30),
        "weekdays": MON_FRI,
        "seats_available": 4,
    },
    {
        "driver_name": "Anna Keller",
        "origin": {"name": "Basel", "lat": 47.5596, "lon": 7.5886},
        "destination": {"name": "Zürich", "lat": 47.3769, "lon": 8.5417},
        "time": time(8, 0),
        "weekdays": (0, 2, 4),
        "seats_available": 1,
    },
    {
        "driver_name": "Luca Bianchi",
        "origin": {"name": "Neuchâtel", "lat": 46.9900, "lon": 6.9293},
        "destination": {"name": "Genève", "lat": 46.2044, "lon": 6.1432},
        "time": time(9, 30),
        "weekdays": (5,),
        "seats_available": 3,
    },
    {
        "driver_name": "Luca Bianchi",
        "origin": {"name": "Genève", "lat": 46.2044, "lon": 6.1432},
        "destination": {"name": "Neuchâtel", "lat": 46.9900, "lon": 6.9293},
        "time": time(18, 0),
        "weekdays": (6,),
        "seats_available": 3,
    },
]


def _departure_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def refresh(now: datetime | None = None) -> tuple[int, int]:
    """Bring demo drives up to date; returns (deleted, created)."""
    db.init_db()
    now = now or datetime.now(LOCAL_TZ).replace(tzinfo=None)
    deleted = db.delete_demo_drives_before(_departure_iso(now))

    created = 0
    routes = {}  # one routing call per template and run, not per drive
    for day in range(DAYS_AHEAD):
        date = now.date() + timedelta(days=day)
        for i, spec in enumerate(DEMO_DRIVES):
            departure = datetime.combine(date, spec["time"])
            if date.weekday() not in spec["weekdays"] or departure <= now:
                continue
            departure_time = _departure_iso(departure)
            if db.demo_drive_exists(spec["driver_name"], departure_time):
                continue
            if i not in routes:
                routes[i] = matching.route_with_fallback([
                    (spec["origin"]["lat"], spec["origin"]["lon"]),
                    (spec["destination"]["lat"], spec["destination"]["lon"]),
                ])
            route = routes[i]
            db.create_drive(
                driver_name=spec["driver_name"],
                origin_name=spec["origin"]["name"],
                origin_lat=spec["origin"]["lat"],
                origin_lon=spec["origin"]["lon"],
                destination_name=spec["destination"]["name"],
                destination_lat=spec["destination"]["lat"],
                destination_lon=spec["destination"]["lon"],
                departure_time=departure_time,
                seats_available=spec["seats_available"],
                route_geometry=route["geometry"],
                route_distance_m=route["distance_m"],
                route_duration_s=route["duration_s"],
                route_source=route["source"],
                is_demo=True,
            )
            created += 1
    return deleted, created


if __name__ == "__main__":
    deleted, created = refresh()
    print(f"Demo drives: {deleted} deleted, {created} created")
