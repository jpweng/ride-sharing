"""Social sign-in (social.py): the accounts a user has at Google or Apple,
each known by the provider's own identifier for them."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS user_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,             -- 'google' or 'apple'
    subject TEXT NOT NULL,              -- the provider's identifier for the person ("sub")
    email TEXT,                         -- as the provider gave it at the first sign-in
    given_name TEXT,                    -- suggested on the profile page, never shown to others
    family_name TEXT,
    created_at TEXT NOT NULL,           -- UTC
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS user_identities_subject ON user_identities (provider, subject);
CREATE INDEX IF NOT EXISTS user_identities_user ON user_identities (user_id);
""")
