"""drives.driver_price_chf, for databases created before drivers set their
price: those drives keep NULL, priced at the default suggestion (see
pricing.py)."""


def apply(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(drives)")}
    if "driver_price_chf" not in columns:
        conn.execute("ALTER TABLE drives ADD COLUMN driver_price_chf REAL")
