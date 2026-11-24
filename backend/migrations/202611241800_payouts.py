"""Paying drivers (payouts.py): each driver's account at Stripe, and what
was sent to it of each payment."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS payout_accounts (
    user_id INTEGER PRIMARY KEY,
    stripe_account_id TEXT NOT NULL UNIQUE,
    submitted INTEGER NOT NULL DEFAULT 0,   -- the driver went through Stripe's pages
    ready INTEGER NOT NULL DEFAULT 0,       -- Stripe accepted them: money can be sent
    created_at TEXT NOT NULL,               -- UTC
    checked_at TEXT,                        -- when Stripe was last asked
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
""")
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(payments)")}
    # What went to the driver of a payment, and when.
    if "transfer_id" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN transfer_id TEXT")
    if "transferred_cts" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN transferred_cts INTEGER NOT NULL DEFAULT 0")
    if "transferred_at" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN transferred_at TEXT")
