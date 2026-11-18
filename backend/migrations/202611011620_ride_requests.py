"""Ride requests (notify.py): a passenger asks for a ride nobody has posted,
drivers who made the trip before are told in their inbox, and can mute a
route. Users get their notification settings."""


def apply(conn):
    conn.executescript("""
-- A passenger's explicit ask for a ride nobody has posted (see notify.py);
-- unlike ride_requests, which logs every search.
CREATE TABLE IF NOT EXISTS open_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passenger_id INTEGER NOT NULL,
    passenger_name TEXT NOT NULL,
    origin_name TEXT NOT NULL,
    origin_lat REAL NOT NULL,
    origin_lon REAL NOT NULL,
    destination_name TEXT NOT NULL,
    destination_lat REAL NOT NULL,
    destination_lon REAL NOT NULL,
    requested_time TEXT NOT NULL,       -- naive Swiss local time
    pickup_radius_km REAL NOT NULL,
    dropoff_radius_km REAL NOT NULL,
    time_tolerance_min INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- 'open' or 'cancelled'; past ones have expired
    extra_round_done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (passenger_id) REFERENCES users(id)
);

-- What the inbox shows. request_for_driver: a passenger's request, sent to
-- a driver who made the trip before; drive_for_passenger: a newly posted
-- drive that fits a passenger's request.
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL,
    recipient_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    request_id INTEGER NOT NULL,        -- the (latest) request it's about
    drive_ids TEXT,                     -- JSON list, drive_for_passenger only
    request_ids TEXT NOT NULL,          -- JSON list: merged requests
    details TEXT,                       -- JSON: the driver's usual trip, suggested departure
    created_at TEXT NOT NULL,
    read_at TEXT,
    acted_at TEXT,
    action TEXT,                        -- 'offer', 'decline', 'mute', 'view'
    FOREIGN KEY (recipient_id) REFERENCES users(id)
);

-- "Not interested in this route": requests starting and ending near these
-- points aren't sent to this driver.
CREATE TABLE IF NOT EXISTS route_mutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    origin_lat REAL NOT NULL,
    origin_lon REAL NOT NULL,
    destination_lat REAL NOT NULL,
    destination_lon REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
""")
    user_columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for column, ddl in {
        "notify_requests": "INTEGER NOT NULL DEFAULT 1",
        # Unanswered notifications before this don't count (see notify.py).
        "backoff_reset_at": "TEXT",
    }.items():
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS notifications_recipient ON notifications (recipient_name, created_at);
        CREATE INDEX IF NOT EXISTS open_requests_time ON open_requests (requested_time);
    """)
