"""The code of a sign-in email (auth.py): typed where the link was asked for,
by someone who can't open the link there (the app installed on an iPhone,
an email read on another device)."""


def apply(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(sign_in_links)")}
    for column, ddl in {
        "code_hash": "TEXT",                             # SHA-256 of "email:code"; NULL when no email was sent
        "code_attempts": "INTEGER NOT NULL DEFAULT 0",   # wrong codes typed while this link could be used
    }.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE sign_in_links ADD COLUMN {column} {ddl}")
