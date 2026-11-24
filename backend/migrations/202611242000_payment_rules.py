"""Payments (payments.py): when a payment's price was settled at the start
of the ride, and when its passenger said the ride didn't take place."""


def apply(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(payments)")}
    # The price per seat falls when more passengers join: what was paid
    # above the final price is refunded once, when the ride starts.
    if "settled_at" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN settled_at TEXT")
    # "The ride didn't take place": the payment is on hold for the team,
    # from disputed_at until dispute_closed_at.
    if "disputed_at" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN disputed_at TEXT")
    if "dispute_closed_at" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN dispute_closed_at TEXT")
