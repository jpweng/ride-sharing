-- The schema as init_db() created it on a new database before the schema
-- moved to backend/migrations/ (commit 8ce3a5c), from sqlite_master.
-- test_migrations.py starts an "existing" database from it.

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
, notify_requests INTEGER NOT NULL DEFAULT 1, backoff_reset_at TEXT, email TEXT, first_name TEXT, last_name TEXT, language TEXT, is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT, adult_confirmed_at TEXT, conduct_version TEXT, conduct_accepted_at TEXT, banned_at TEXT);

CREATE TABLE drives (
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

CREATE TABLE demo_routes (
    origin_name TEXT NOT NULL,
    destination_name TEXT NOT NULL,
    geometry TEXT NOT NULL,     -- JSON list of [lon, lat] pairs
    distance_m REAL NOT NULL,
    duration_s REAL NOT NULL,
    PRIMARY KEY (origin_name, destination_name)
);

CREATE TABLE ride_requests (
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

CREATE TABLE open_requests (
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

CREATE TABLE notifications (
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

CREATE TABLE route_mutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    origin_lat REAL NOT NULL,
    origin_lon REAL NOT NULL,
    destination_lat REAL NOT NULL,
    destination_lon REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE sign_in_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT UNIQUE,             -- SHA-256 of the token in the link
    email TEXT NOT NULL,
    ip TEXT,
    language TEXT NOT NULL,
    created_at TEXT NOT NULL,           -- UTC
    expires_at TEXT NOT NULL,           -- UTC
    used_at TEXT
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,    -- SHA-256 of the cookie's value
    user_id INTEGER NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0, -- "view as a demo driver": read-only
    created_at TEXT NOT NULL,           -- UTC
    last_seen_at TEXT NOT NULL,         -- UTC, updated at most once a day
    expires_at TEXT NOT NULL,           -- UTC; sliding, see auth.py
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX drives_departure_time ON drives (departure_time);

CREATE INDEX drives_driver_name ON drives (driver_name);

CREATE INDEX notifications_recipient ON notifications (recipient_name, created_at);

CREATE INDEX open_requests_time ON open_requests (requested_time);

CREATE UNIQUE INDEX users_email ON users (email) WHERE email IS NOT NULL;

CREATE INDEX sign_in_links_email ON sign_in_links (email, created_at);

CREATE INDEX sign_in_links_ip ON sign_in_links (ip, created_at);

CREATE INDEX sessions_user ON sessions (user_id);
