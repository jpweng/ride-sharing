"""
Admin tasks without an admin UI. Run on the server (Session Manager shell):

    docker compose exec app python backend/admin.py ban someone@example.ch
    docker compose exec app python backend/admin.py unban someone@example.ch

A banned account's sessions end at once; it can't sign in again (no link is
sent, and an earlier link is refused) until it's unbanned. Its drives and
requests stay until they expire; cancel or delete them separately if needed.
"""
import argparse
import sys

import auth
import database as db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["ban", "unban"])
    parser.add_argument("email")
    args = parser.parse_args(argv)

    db.init_db()
    email = auth.normalize_email(args.email)
    if not db.set_banned(email, args.action == "ban"):
        print(f"No account with the email {email}", file=sys.stderr)
        return 1
    print(f"{args.action}ned: {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
