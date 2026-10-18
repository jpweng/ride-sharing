"""
Seeds a handful of demo drives across Swiss towns so the "browse" and
"match" use cases have something to show immediately.

Run with the backend on PYTHONPATH, e.g.:
    PYTHONPATH=backend uv run python backend/seed_data.py
"""
from datetime import datetime, timedelta

import database as db
import matching

DEMO_DRIVES = [
    {
        "driver_name": "Jean",
        "origin": {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323},
        "destination": {"name": "Genève", "lat": 46.2044, "lon": 6.1432},
        "hours_from_now": 14,
        "seats_available": 3,
    },
    {
        "driver_name": "Sophie",
        "origin": {"name": "Zürich", "lat": 47.3769, "lon": 8.5417},
        "destination": {"name": "Bern", "lat": 46.9480, "lon": 7.4474},
        "hours_from_now": 20,
        "seats_available": 2,
    },
    {
        "driver_name": "Marco",
        "origin": {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619},
        "destination": {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323},
        "hours_from_now": 16,
        "seats_available": 4,
    },
    {
        "driver_name": "Anna",
        "origin": {"name": "Basel", "lat": 47.5596, "lon": 7.5886},
        "destination": {"name": "Zürich", "lat": 47.3769, "lon": 8.5417},
        "hours_from_now": 26,
        "seats_available": 1,
    },
    {
        "driver_name": "Luca",
        "origin": {"name": "Neuchâtel", "lat": 46.9900, "lon": 6.9293},
        "destination": {"name": "Genève", "lat": 46.2044, "lon": 6.1432},
        "hours_from_now": 18,
        "seats_available": 3,
    },
]


def seed():
    db.init_db()
    now = datetime.now()
    for spec in DEMO_DRIVES:
        departure_time = (now + timedelta(hours=spec["hours_from_now"])).isoformat(timespec="seconds")
        route = matching.route_with_fallback([
            (spec["origin"]["lat"], spec["origin"]["lon"]),
            (spec["destination"]["lat"], spec["destination"]["lon"]),
        ])
        drive_id = db.create_drive(
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
        )
        print(f"Seeded drive #{drive_id}: {spec['origin']['name']} -> {spec['destination']['name']} "
              f"({route['source']}, {route['distance_m']/1000:.1f} km)")


if __name__ == "__main__":
    seed()
