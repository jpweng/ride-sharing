"""
A driver's areas: the places they usually drive from or to (home, work,
...), each a point with a radius the driver chooses. See docs/driver-areas.md.

A driver is told about the ride requests that start or end in one of
their areas (drivers_near, used by notify.py), on top of the drivers who
made the trip before.

An area belongs to one user and is shown to nobody else. It stays until its
owner removes it or deletes their account (database.delete_account).
"""
import database as db
import matching

# How many areas a driver can save: home, work and a few more, without
# turning into "everywhere".
MAX_AREAS = 5
# The radii a driver can choose from, in km. 10 km is the default: car
# commutes in Switzerland average about 15 km, so it covers the usual
# pickups around a home or a workplace without taking in a whole canton.
RADIUS_OPTIONS_KM = (5, 10, 20, 30)
DEFAULT_RADIUS_KM = 10


def list_for(user_id: int) -> list[dict]:
    """A user's areas, oldest first."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM driver_areas WHERE user_id = ? ORDER BY id", (user_id,))
        return [dict(r) for r in rows]


def get(area_id: int, user_id: int) -> dict | None:
    """One of the user's areas; None if there is none with this id (someone
    else's included)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM driver_areas WHERE id = ? AND user_id = ?", (area_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def add(user_id: int, name: str, lat: float, lon: float, radius_km: float) -> dict | None:
    """Save an area; None if the user already has MAX_AREAS. The count and
    the insert are one statement, so two requests at once can't go over."""
    with db.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO driver_areas (user_id, name, lat, lon, radius_km, created_at)
            SELECT ?, ?, ?, ?, ?, ?
            WHERE (SELECT COUNT(*) FROM driver_areas WHERE user_id = ?) < ?
            """,
            (user_id, name.strip(), lat, lon, radius_km, db.now_iso(), user_id, MAX_AREAS),
        )
        if cur.rowcount != 1:
            return None
        area_id = cur.lastrowid
    return get(area_id, user_id)


def set_radius(area_id: int, user_id: int, radius_km: float) -> dict | None:
    """Change an area's radius; None if the user has no such area."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE driver_areas SET radius_km = ? WHERE id = ? AND user_id = ?", (radius_km, area_id, user_id)
        )
    return get(area_id, user_id)


def remove(area_id: int, user_id: int) -> bool:
    """Remove an area; False if the user has no such area."""
    with db.get_conn() as conn:
        return conn.execute(
            "DELETE FROM driver_areas WHERE id = ? AND user_id = ?", (area_id, user_id)
        ).rowcount == 1


# ---------------------------------------------------------------------
# Ride requests in an area
# ---------------------------------------------------------------------
def ends_inside(area: dict, request: dict) -> list[str]:
    """Which ends of a ride request are inside the area: "origin",
    "destination", both or none."""
    return [
        end for end in ("origin", "destination")
        if matching.haversine_km(area["lat"], area["lon"], request[f"{end}_lat"], request[f"{end}_lon"])
        <= area["radius_km"]
    ]


def _fit(area: dict, request: dict) -> dict | None:
    """How a request fits an area ({area, ends, distance_km}), or None if
    neither end is inside. distance_km: from the area's centre to the
    nearest end inside it."""
    ends = ends_inside(area, request)
    if not ends:
        return None
    distance = min(
        matching.haversine_km(area["lat"], area["lon"], request[f"{end}_lat"], request[f"{end}_lon"])
        for end in ends
    )
    return {
        "area": {k: area[k] for k in ("id", "name", "lat", "lon", "radius_km")},
        "ends": ends,
        "distance_km": round(distance, 1),
    }


def _best(fits: list[dict]) -> dict:
    """Of a driver's areas a request fits, the one to name, with the ends
    all of them cover between them: a driver with one area at the start
    (home) and another at the destination (work) covers both."""
    best = min(fits, key=lambda fit: (-len(fit["ends"]), fit["distance_km"]))
    covered = {end for fit in fits for end in fit["ends"]}
    return {
        **best,
        "ends": [end for end in ("origin", "destination") if end in covered],
        "area_ids": [fit["area"]["id"] for fit in fits],
    }


def best_fit(user_areas: list[dict], request: dict) -> dict | None:
    """How a request fits a driver's areas ({area, ends, distance_km,
    area_ids: every area of theirs it touches}, see _best); None if it
    starts and ends outside them all."""
    fits = [fit for fit in (_fit(area, request) for area in user_areas) if fit]
    return _best(fits) if fits else None


def drivers_near(request: dict) -> list[dict]:
    """The drivers with an area a ride request starts or ends in, best
    first: [{driver_name, area, ends, distance_km}], one entry per driver.
    Those whose areas cover both ends come before those covering one, then
    the nearest to their area's centre. Accounts only, not banned ones; the
    passenger themself is left to the caller."""
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT a.*, u.name AS driver_name FROM driver_areas a JOIN users u ON u.id = a.user_id
            WHERE u.banned_at IS NULL AND u.is_demo = 0 ORDER BY a.id
            """
        )]
    by_driver = {}
    for row in rows:
        by_driver.setdefault(row["driver_name"], []).append(row)
    drivers = []
    for name, user_areas in by_driver.items():
        fit = best_fit(user_areas, request)
        if fit:
            drivers.append({"driver_name": name, **fit})
    return sorted(drivers, key=lambda d: (-len(d["ends"]), d["distance_km"], d["driver_name"]))
