"""Sign-in by email link (auth.py): accounts on users, the sign-in links
asked for, and sessions."""


def apply(conn):
    user_columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for column, ddl in {
        "email": "TEXT",                    # lower-case; NULL for demo and legacy users
        "first_name": "TEXT",
        "last_name": "TEXT",
        "language": "TEXT",                 # de/fr/it/en, for emails
        "is_demo": "INTEGER NOT NULL DEFAULT 0",
        "created_at": "TEXT",
        "adult_confirmed_at": "TEXT",       # "I am 18 or older", UTC
        "conduct_version": "TEXT",          # code of conduct accepted (conduct.html)
        "conduct_accepted_at": "TEXT",
        "banned_at": "TEXT",                # set by an admin (admin.py): no sign-in
    }.items():
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
    if "is_demo" not in user_columns:
        # Demo drivers from before users.is_demo: those with demo drives.
        conn.execute(
            "UPDATE users SET is_demo = 1 WHERE id IN (SELECT driver_id FROM drives WHERE is_demo = 1)"
        )
    conn.executescript("""
-- Sign-in links asked for (see auth.py), also the log the per-email and
-- per-IP limits count. token_hash is NULL when no email was sent (over the
-- per-email limit, or a banned account). Deleted after a day.
CREATE TABLE IF NOT EXISTS sign_in_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT UNIQUE,             -- SHA-256 of the token in the link
    email TEXT NOT NULL,
    ip TEXT,
    language TEXT NOT NULL,
    created_at TEXT NOT NULL,           -- UTC
    expires_at TEXT NOT NULL,           -- UTC
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,    -- SHA-256 of the cookie's value
    user_id INTEGER NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0, -- "view as a demo driver": read-only
    created_at TEXT NOT NULL,           -- UTC
    last_seen_at TEXT NOT NULL,         -- UTC, updated at most once a day
    expires_at TEXT NOT NULL,           -- UTC; sliding, see auth.py
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users (email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS sign_in_links_email ON sign_in_links (email, created_at);
CREATE INDEX IF NOT EXISTS sign_in_links_ip ON sign_in_links (ip, created_at);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id);
""")
