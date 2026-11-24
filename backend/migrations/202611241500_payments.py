"""Payments (payments.py): one row per booking whose passenger pays through
the site, from the driver's acceptance on.

A payment outlives its booking and its drive (bookings go with their drive,
30 days after its departure): it is the record of money that moved, kept as
long as bookkeeping rules ask. So it has no foreign key to them and keeps
the trip it was for. It holds no names; when a user's account is deleted,
the foreign keys leave the row without that user."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE,
    drive_id INTEGER NOT NULL,
    passenger_id INTEGER,               -- NULL once that account is deleted
    driver_id INTEGER,
    -- 'due' (accepted, waiting for the passenger to pay), 'paid', or
    -- 'cancelled' (never paid: the booking ended first). A paid one whose
    -- booking ended before the ride has ended_at, and a refund due.
    status TEXT NOT NULL DEFAULT 'due',
    pay_by TEXT NOT NULL,               -- naive Swiss local time: unpaid by then, the booking is cancelled
    seats INTEGER NOT NULL,
    -- Centimes. amount_cts: what the passenger paid, set when they did.
    -- refund_due_cts: how much of it is to go back to them in all;
    -- refunded_cts: how much Stripe was told to send back so far.
    amount_cts INTEGER,
    refund_due_cts INTEGER NOT NULL DEFAULT 0,
    refunded_cts INTEGER NOT NULL DEFAULT 0,
    checkout_session_id TEXT,           -- the Checkout page open or completed last
    payment_intent_id TEXT,
    charge_id TEXT,
    -- The trip it was for, as it was when the driver accepted.
    origin_name TEXT NOT NULL,
    destination_name TEXT NOT NULL,
    pickup_time TEXT NOT NULL,          -- naive Swiss local time
    dropoff_time TEXT NOT NULL,
    created_at TEXT NOT NULL,           -- UTC
    paid_at TEXT,
    cancelled_at TEXT,
    ended_at TEXT,                      -- paid, and the booking ended before the ride
    refunded_at TEXT,                   -- the last refund
    FOREIGN KEY (passenger_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (driver_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS payments_drive ON payments (drive_id);
CREATE INDEX IF NOT EXISTS payments_status ON payments (status, pay_by);
CREATE INDEX IF NOT EXISTS payments_checkout ON payments (checkout_session_id);
""")
