"""Bookings (bookings.py): a passenger asks for seats on a drive, the driver
accepts or declines. Deleted with their drive."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id INTEGER NOT NULL,
    passenger_id INTEGER NOT NULL,
    passenger_name TEXT NOT NULL,
    seats INTEGER NOT NULL DEFAULT 1,
    -- 'requested', then 'accepted' or 'declined' by the driver;
    -- 'cancelled_by_passenger' or 'cancelled_by_driver' afterwards.
    status TEXT NOT NULL DEFAULT 'requested',
    -- Where the passenger starts and is going (the drive's own ends when
    -- they take the whole drive).
    origin_name TEXT NOT NULL,
    origin_lat REAL NOT NULL,
    origin_lon REAL NOT NULL,
    destination_name TEXT NOT NULL,
    destination_lat REAL NOT NULL,
    destination_lon REAL NOT NULL,
    -- Where the drive's route passes nearest to them, and when (naive
    -- Swiss local time); gap_km: how far that is from their own point.
    pickup_lat REAL NOT NULL,
    pickup_lon REAL NOT NULL,
    pickup_time TEXT NOT NULL,
    pickup_gap_km REAL NOT NULL,
    dropoff_lat REAL NOT NULL,
    dropoff_lon REAL NOT NULL,
    dropoff_time TEXT NOT NULL,
    dropoff_gap_km REAL NOT NULL,
    ride_km REAL NOT NULL,
    passenger_note TEXT,                -- with the request, for the driver
    driver_note TEXT,                   -- with the acceptance, for the passenger
    created_at TEXT NOT NULL,           -- UTC
    decided_at TEXT,                    -- accepted or declined, UTC
    cancelled_at TEXT,                  -- UTC
    FOREIGN KEY (drive_id) REFERENCES drives(id) ON DELETE CASCADE,
    FOREIGN KEY (passenger_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS bookings_drive ON bookings (drive_id);
CREATE INDEX IF NOT EXISTS bookings_passenger ON bookings (passenger_id);
""")
