"""Payments (payments.py): the service fee that was part of what the
passenger paid."""


def apply(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(payments)")}
    # Centimes, included in amount_cts; 0 when no fee is set.
    if "fee_cts" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN fee_cts INTEGER NOT NULL DEFAULT 0")
