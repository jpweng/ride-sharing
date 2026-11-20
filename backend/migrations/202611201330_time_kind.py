"""A search or a ride request can be for a time to arrive by instead of a
time to leave at (matching.py): time_kind, "depart" for the rows made before.
A ride request to arrive by also keeps when the passenger's own trip has to
start for that (leave_by)."""


def apply(conn):
    for table, columns in {
        "ride_requests": {"time_kind": "TEXT NOT NULL DEFAULT 'depart'"},
        "open_requests": {"time_kind": "TEXT NOT NULL DEFAULT 'depart'", "leave_by": "TEXT"},
    }.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, ddl in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
