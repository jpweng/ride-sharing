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
    FOREIGN KEY (driver_id) REFERENCES users(id)
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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_or_create_user(name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Name must not be empty")
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO users (name) VALUES (?)", (name,))
        return cur.lastrowid


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
) -> int:
    driver_id = get_or_create_user(driver_name)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO drives (
                driver_id, driver_name, origin_name, origin_lat, origin_lon,
                destination_name, destination_lat, destination_lon,
                departure_time, seats_available, route_geometry,
                route_distance_m, route_duration_s, route_source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                driver_id, driver_name, origin_name, origin_lat, origin_lon,
                destination_name, destination_lat, destination_lon,
                departure_time, seats_available,
                json.dumps(route_geometry) if route_geometry is not None else None,
                route_distance_m, route_duration_s, route_source, now_iso(),
            ),
        )
        return cur.lastrowid


def list_drives(upcoming_only: bool = True, driver_name: str | None = None) -> list[dict]:
    clauses, params = [], []
    if upcoming_only:
        clauses.append("departure_time >= ?")
        params.append(now_iso())
    if driver_name is not None:
        clauses.append("driver_name = ?")
        params.append(driver_name.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM drives {where} ORDER BY departure_time ASC", params
        ).fetchall()
        return [_drive_row_to_dict(r) for r in rows]


def get_drive(drive_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM drives WHERE id = ?", (drive_id,)).fetchone()
        return _drive_row_to_dict(row) if row else None


def delete_drive(drive_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM drives WHERE id = ?", (drive_id,))


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
    passenger_id = get_or_create_user(passenger_name)
    with get_conn() as conn:
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
