"""drives.is_demo, for databases created before it: 1 marks the fictional
drives managed by demo_drives.py."""


def apply(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(drives)")}
    if "is_demo" not in columns:
        conn.execute("ALTER TABLE drives ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
