"""Profiles (profiles.py): what a user says about themselves and, for
drivers, their car, and which drivers a passenger may see (the candidates
of their searches). Both go with the user row (ON DELETE CASCADE)."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY,
    about TEXT,                         -- a few words about oneself
    -- Each NULL (not said), 1 or 0.
    talkative INTEGER,
    sleeps INTEGER,                     -- likes to sleep in the car
    smoker INTEGER,
    pets INTEGER,                       -- travels with a pet
    music INTEGER,                      -- likes music in the car
    -- A driver's car, each optional.
    car_make TEXT,
    car_model TEXT,
    car_year INTEGER,
    car_color TEXT,
    photo_key TEXT,                     -- where the photo is stored (photos.py); NULL: none
    updated_at TEXT NOT NULL,           -- UTC
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- A passenger may see the profile of a driver one of their searches found:
-- one row per (viewer, driver), kept until either account is deleted.
CREATE TABLE IF NOT EXISTS profile_grants (
    viewer_id INTEGER NOT NULL,
    subject_id INTEGER NOT NULL,
    PRIMARY KEY (viewer_id, subject_id),
    FOREIGN KEY (viewer_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (subject_id) REFERENCES users(id) ON DELETE CASCADE
);
""")
