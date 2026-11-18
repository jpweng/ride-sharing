"""Users, drives and passengers' searches (ride_requests).

drives is created as it is today; databases from before its later columns
(is_demo, driver_price_chf) get them from the migrations that added them.
users starts with its first two columns; the others are added by later
migrations, on new databases too.
"""


def apply(conn):
    conn.executescript("""
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
    driver_price_chf REAL,      -- the driver's price per passenger, one passenger, whole drive
                                -- (see pricing.py); NULL: the default suggestion
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
""")
