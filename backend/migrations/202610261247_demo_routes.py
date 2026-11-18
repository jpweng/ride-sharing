"""One road route per demo trip, shared by all its demo drives (which store
no geometry of their own): thousands of copies would fill the disk.

Databases from before demo_routes: keep one copy of each demo trip's road
route there, drop the copies on the drives, and give their space back to
the disk (VACUUM). On every later start there is nothing left to move.
"""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS demo_routes (
    origin_name TEXT NOT NULL,
    destination_name TEXT NOT NULL,
    geometry TEXT NOT NULL,     -- JSON list of [lon, lat] pairs
    distance_m REAL NOT NULL,
    duration_s REAL NOT NULL,
    PRIMARY KEY (origin_name, destination_name)
);
""")
    conn.execute("""
        INSERT OR IGNORE INTO demo_routes (origin_name, destination_name, geometry, distance_m, duration_s)
        SELECT origin_name, destination_name, route_geometry, route_distance_m, route_duration_s
        FROM drives
        WHERE is_demo = 1 AND route_source = 'osrm' AND route_geometry IS NOT NULL
    """)
    moved = conn.execute("""
        UPDATE drives SET route_geometry = NULL
        WHERE is_demo = 1 AND route_source = 'osrm' AND route_geometry IS NOT NULL
    """).rowcount
    if moved:
        # VACUUM can't run inside a transaction.
        conn.commit()
        conn.execute("VACUUM")
