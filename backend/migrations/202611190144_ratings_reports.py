"""Ratings after a ride (ratings.py) and reports of a problem (reports.py).

When a user row is deleted (their account, or a name nothing references any
more), the foreign keys do the rest: the ratings they received go with
them, those they gave stay without their author, and reports keep the
names they were filed with."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,        -- the ride it's about (bookings go with their drive; ratings stay)
    rater_id INTEGER,                   -- NULL once the author's account is deleted
    rated_id INTEGER NOT NULL,
    rated_role TEXT NOT NULL,           -- 'driver' or 'passenger': what the rated person was on that ride
    stars INTEGER NOT NULL,             -- 1 to 5
    created_at TEXT NOT NULL,           -- UTC
    FOREIGN KEY (rater_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (rated_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ratings_once ON ratings (booking_id, rater_id);
CREATE INDEX IF NOT EXISTS ratings_rated ON ratings (rated_id, rated_role);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    reporter_id INTEGER,                -- NULL once that account is deleted
    reporter_name TEXT NOT NULL,
    reported_id INTEGER,
    reported_name TEXT NOT NULL,
    reason TEXT NOT NULL,               -- one of reports.REASONS
    details TEXT,
    context TEXT NOT NULL,              -- JSON: the ride as it was (the drive and booking are deleted later)
    created_at TEXT NOT NULL,           -- UTC
    closed_at TEXT,                     -- handled (admin.py)
    closing_note TEXT,
    FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (reported_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS reports_once ON reports (booking_id, reporter_id);
""")
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(bookings)")}
    if "rating_asked_at" not in columns:
        # When both sides were asked how the ride was (ratings.ask_after_rides).
        conn.execute("ALTER TABLE bookings ADD COLUMN rating_asked_at TEXT")
