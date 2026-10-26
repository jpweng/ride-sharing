"""
SQLite data access layer for the ride-sharing prototype.

Kept deliberately simple: plain `sqlite3` (no ORM), one module-level
connection helper, and a handful of small functions used by the API layer.
This is a prototype/demo database, not a production schema.
"""
import sqlite3
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

# Overridable so the container can keep the database on a persistent volume
# (EFS in AWS); defaults to the repo root for local development.
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rideshare.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS drives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_id INTEGER NOT NULL,
    driver_name TEXT NOT NULL,
    origin_name TEXT NOT NULL,
    origin_lat REAL NOT NULL,
    origin_lon REAL NOT NULL,
    destination_name TEXT NOT NULL,
    destination_lat REAL NOT NULL,
    destination_lon REAL NOT NULL,
    departure_time TEXT NOT NULL,
    seats_available INTEGER NOT NULL DEFAULT 1,
    route_geometry TEXT,        -- JSON list of [lon, lat] pairs, or NULL
    route_distance_m REAL,      -- driving distance, meters
    route_duration_s REAL,      -- driving duration, seconds
    route_source TEXT,          -- 'osrm' or 'haversine' (fallback)
    created_at TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0,  -- 1: fictional, managed by demo_drives.py
    FOREIGN KEY (driver_id) REFERENCES users(id)
);

-- One road route per demo trip, shared by all its demo drives (which store
-- no geometry of their own): thousands of copies would fill the disk.
CREATE TABLE IF NOT EXISTS demo_routes (
    origin_name TEXT NOT NULL,
    destination_name TEXT NOT NULL,
    geometry TEXT NOT NULL,     -- JSON list of [lon, lat] pairs
    distance_m REAL NOT NULL,
    duration_s REAL NOT NULL,
    PRIMARY KEY (origin_name, destination_name)
);

CREATE TABLE IF NOT EXISTS ride_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passenger_id INTEGER NOT NULL,
    passenger_name TEXT NOT NULL,
    origin_name TEXT NOT NULL,
    origin_lat REAL NOT NULL,
    origin_lon REAL NOT NULL,
    destination_name TEXT NOT NULL,
    destination_lat REAL NOT NULL,
    destination_lon REAL NOT NULL,
    requested_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (passenger_id) REFERENCES users(id)
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Databases created before is_demo existed.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(drives)")}
        if "is_demo" not in columns:
            conn.execute("ALTER TABLE drives ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS drives_departure_time ON drives (departure_time);
            CREATE INDEX IF NOT EXISTS drives_driver_name ON drives (driver_name);
        """)
        moved = _move_demo_geometry_to_demo_routes(conn)
    if moved:
        # Give the space of the removed copies back to the disk.
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("VACUUM")


def _move_demo_geometry_to_demo_routes(conn: sqlite3.Connection) -> int:
    """Databases from before demo_routes: keep one copy of each demo trip's
    road route there and drop the copies on the drives. Returns how many
    drives were changed."""
    conn.execute("""
        INSERT OR IGNORE INTO demo_routes (origin_name, destination_name, geometry, distance_m, duration_s)
        SELECT origin_name, destination_name, route_geometry, route_distance_m, route_duration_s
        FROM drives
        WHERE is_demo = 1 AND route_source = 'osrm' AND route_geometry IS NOT NULL
    """)
    return conn.execute("""
        UPDATE drives SET route_geometry = NULL
        WHERE is_demo = 1 AND route_source = 'osrm' AND route_geometry IS NOT NULL
    """).rowcount


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_or_create_user(name: str) -> int:
    with get_conn() as conn:
        return _get_or_create_user(conn, name)


def _get_or_create_user(conn: sqlite3.Connection, name: str) -> int:
    # Writing first opens the write transaction, so delete_inactive_users()
    # can't remove the user before the caller's insert in the same
    # transaction references it.
    name = name.strip()
    if not name:
        raise ValueError("Name must not be empty")
    conn.execute("INSERT INTO users (name) VALUES (?) ON CONFLICT (name) DO NOTHING", (name,))
    return conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()["id"]


def create_drive(
    driver_name: str,
    origin_name: str,
    origin_lat: float,
    origin_lon: float,
    destination_name: str,
    destination_lat: float,
    destination_lon: float,
    departure_time: str,
    seats_available: int,
    route_geometry: list | None,
    route_distance_m: float | None,
    route_duration_s: float | None,
    route_source: str,
    is_demo: bool = False,
) -> int:
    fields = dict(locals())
    with get_conn() as conn:
        return _insert_drive(conn, **fields)


def create_drives(drives: list[dict]) -> int:
    """Insert many drives (create_drive()'s arguments, as dicts) in one
    transaction; returns how many."""
    with get_conn() as conn:
        for drive in drives:
            _insert_drive(conn, **drive)
    return len(drives)


def _insert_drive(conn: sqlite3.Connection, *, driver_name, route_geometry, is_demo=False, **fields) -> int:
    driver_id = _get_or_create_user(conn, driver_name)
    cur = conn.execute(
        """
        INSERT INTO drives (
            driver_id, driver_name, origin_name, origin_lat, origin_lon,
            destination_name, destination_lat, destination_lon,
            departure_time, seats_available, route_geometry,
            route_distance_m, route_duration_s, route_source, created_at,
            is_demo
        ) VALUES (
            :driver_id, :driver_name, :origin_name, :origin_lat, :origin_lon,
            :destination_name, :destination_lat, :destination_lon,
            :departure_time, :seats_available, :route_geometry,
            :route_distance_m, :route_duration_s, :route_source, :created_at,
            :is_demo
        )
        """,
        {
            **fields,
            "driver_id": driver_id,
            "driver_name": driver_name,
            "route_geometry": json.dumps(route_geometry) if route_geometry is not None else None,
            "created_at": now_iso(),
            "is_demo": int(is_demo),
        },
    )
    return cur.lastrowid


# Every drives column but route_geometry, which can be ~60 KB per drive.
_DRIVE_COLUMNS_WITHOUT_GEOMETRY = ", ".join(f"d.{c}" for c in (
    "id", "driver_id", "driver_name", "origin_name", "origin_lat", "origin_lon",
    "destination_name", "destination_lat", "destination_lon", "departure_time",
    "seats_available", "route_distance_m", "route_duration_s", "route_source",
    "created_at", "is_demo",
))
# Demo drives get their trip's shared route from demo_routes.
_DRIVE_COLUMNS = _DRIVE_COLUMNS_WITHOUT_GEOMETRY + ", COALESCE(d.route_geometry, r.geometry) AS route_geometry"
_DRIVES_WITH_ROUTES = """
    drives d LEFT JOIN demo_routes r
    ON d.is_demo = 1 AND r.origin_name = d.origin_name AND r.destination_name = d.destination_name
"""


def list_drives(
    upcoming_only: bool = True,
    driver_name: str | None = None,
    with_geometry: bool = True,
    limit: int | None = None,
    departing_from: str | None = None,
    departing_until: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if upcoming_only:
        clauses.append("d.departure_time >= ?")
        params.append(now_iso())
    if driver_name is not None:
        clauses.append("d.driver_name = ?")
        params.append(driver_name.strip())
    if departing_from is not None:
        clauses.append("d.departure_time >= ?")
        params.append(departing_from)
    if departing_until is not None:
        clauses.append("d.departure_time <= ?")
        params.append(departing_until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    select = (
        f"SELECT {_DRIVE_COLUMNS} FROM {_DRIVES_WITH_ROUTES}" if with_geometry
        else f"SELECT {_DRIVE_COLUMNS_WITHOUT_GEOMETRY} FROM drives d"
    )
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(f"{select} {where} ORDER BY d.departure_time ASC {limit_sql}", params).fetchall()
        return [_drive_row_to_dict(r) for r in rows]


def get_drive(drive_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {_DRIVE_COLUMNS} FROM {_DRIVES_WITH_ROUTES} WHERE d.id = ?", (drive_id,)
        ).fetchone()
        return _drive_row_to_dict(row) if row else None


def delete_drive(drive_id: int) -> None:
    delete_drives([drive_id])


def delete_drives(drive_ids: list[int]) -> int:
    with get_conn() as conn:
        conn.executemany("DELETE FROM drives WHERE id = ?", [(i,) for i in drive_ids])
    return len(drive_ids)


def find_demo_route(origin_name: str, destination_name: str) -> dict | None:
    """The road route stored for a demo trip, in matching.route_with_fallback()'s
    shape; None if there's none."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM demo_routes WHERE origin_name = ? AND destination_name = ?",
            (origin_name, destination_name),
        ).fetchone()
    if row is None:
        return None
    return {
        "geometry": json.loads(row["geometry"]),
        "distance_m": row["distance_m"],
        "duration_s": row["duration_s"],
        "source": "osrm",
    }


def save_demo_route(origin_name: str, destination_name: str, route: dict) -> None:
    """Store a demo trip's road route (from OSRM) for all its demo drives."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO demo_routes (origin_name, destination_name, geometry, distance_m, duration_s)
            VALUES (?, ?, ?, ?, ?)
            """,
            (origin_name, destination_name, json.dumps(route["geometry"]), route["distance_m"], route["duration_s"]),
        )


def delete_demo_drives_before(departure_time: str) -> int:
    """Delete demo drives departing before `departure_time`; returns how many."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM drives WHERE is_demo = 1 AND departure_time < ?", (departure_time,)
        )
        return cur.rowcount


def delete_visitor_drives_before(departure_time: str) -> int:
    """Delete non-demo drives departing before `departure_time`; returns how many."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM drives WHERE is_demo = 0 AND departure_time < ?", (departure_time,)
        )
        return cur.rowcount


def _drive_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("route_geometry"):
        d["route_geometry"] = json.loads(d["route_geometry"])
    return d


def save_ride_request(
    passenger_name: str,
    origin_name: str,
    origin_lat: float,
    origin_lon: float,
    destination_name: str,
    destination_lat: float,
    destination_lon: float,
    requested_time: str,
) -> int:
    with get_conn() as conn:
        passenger_id = _get_or_create_user(conn, passenger_name)
        cur = conn.execute(
            """
            INSERT INTO ride_requests (
                passenger_id, passenger_name, origin_name, origin_lat, origin_lon,
                destination_name, destination_lat, destination_lon,
                requested_time, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                passenger_id, passenger_name, origin_name, origin_lat, origin_lon,
                destination_name, destination_lat, destination_lon,
                requested_time, now_iso(),
            ),
        )
        return cur.lastrowid


def list_recent_ride_requests(passenger_name: str, limit: int = 5) -> list[dict]:
    """
    A passenger's most recent searches, newest first, with repeats of the
    same origin -> destination collapsed (every "Find a ride" click saves a
    row, so the raw table is full of near-duplicates).
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ride_requests WHERE passenger_name = ? ORDER BY id DESC LIMIT 50",
            (passenger_name.strip(),),
        ).fetchall()
    seen, result = set(), []
    for r in rows:
        key = (r["origin_name"], r["destination_name"])
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(r))
        if len(result) >= limit:
            break
    return result


def delete_ride_requests_before(created_at: str) -> int:
    """Delete searches made before `created_at` (UTC ISO 8601); returns how many."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM ride_requests WHERE created_at < ?", (created_at,))
        return cur.rowcount


def delete_inactive_users() -> int:
    """Delete users with no drives and no searches left; returns how many."""
    with get_conn() as conn:
        cur = conn.execute("""
            DELETE FROM users
            WHERE id NOT IN (SELECT driver_id FROM drives)
              AND id NOT IN (SELECT passenger_id FROM ride_requests)
        """)
        return cur.rowcount
