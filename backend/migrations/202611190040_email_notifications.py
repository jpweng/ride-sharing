"""Notifications by email (notify_email.py): each user's switch and
unsubscribe token, and what became of each notification's email."""


def apply(conn):
    user_columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for column, ddl in {
        "notify_email": "INTEGER NOT NULL DEFAULT 1",   # also send notifications by email
        "email_token": "TEXT",                          # in the emails' "turn these off" link
    }.items():
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")

    notification_columns = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)")}
    if "email_state" not in notification_columns:
        # NULL: still to decide or to send; then 'sent', 'skipped' or 'failed'.
        conn.execute("ALTER TABLE notifications ADD COLUMN email_state TEXT")
        # Notifications from before emails existed are not sent after the fact.
        conn.execute("UPDATE notifications SET email_state = 'skipped'")
    if "email_attempts" not in notification_columns:
        conn.execute("ALTER TABLE notifications ADD COLUMN email_attempts INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_token ON users (email_token) WHERE email_token IS NOT NULL")
