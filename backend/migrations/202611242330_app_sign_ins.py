"""Sign-in with Google or Apple from the iOS app (app_sign_in.py): the
one-time codes that carry a sign-in from the system's sign-in sheet into the
app, each for two minutes at most."""


def apply(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS app_sign_ins (
    code_hash TEXT PRIMARY KEY,   -- SHA-256 of the code; the code itself is never stored
    user_id INTEGER NOT NULL,
    challenge TEXT NOT NULL,      -- SHA-256 of the app's verifier, base64url
    language TEXT NOT NULL,
    expires_at TEXT NOT NULL,     -- UTC
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
""")
