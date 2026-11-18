"""Driver areas (areas.py): the places a driver usually drives from or to,
each with a radius."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS driver_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,                 -- the place, as the driver picked it
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    radius_km REAL NOT NULL,            -- one of areas.RADIUS_OPTIONS_KM
    created_at TEXT NOT NULL,           -- UTC
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS driver_areas_user ON driver_areas (user_id);
""")
