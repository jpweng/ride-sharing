"""
A driver's areas: the places they usually drive from or to (home, work,
...), each a point with a radius the driver chooses. See docs/driver-areas.md.

For now they are only stored and shown to their owner. They are meant for
telling a driver about the ride requests that start or end in one of their
areas, and for letting them browse those requests.

An area belongs to one user and is shown to nobody else. It stays until its
owner removes it or deletes their account (database.delete_account).
"""
import database as db

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
