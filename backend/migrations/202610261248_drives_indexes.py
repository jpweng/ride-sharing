"""Indexes for listing upcoming drives and a driver's own drives."""


def apply(conn):
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS drives_departure_time ON drives (departure_time);
        CREATE INDEX IF NOT EXISTS drives_driver_name ON drives (driver_name);
    """)
