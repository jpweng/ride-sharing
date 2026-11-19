"""
Admin tasks without an admin UI. Run on the server (Session Manager shell):

    docker compose exec app python backend/admin.py ban someone@example.ch
    docker compose exec app python backend/admin.py unban someone@example.ch
    docker compose exec app python backend/admin.py reports
    docker compose exec app python backend/admin.py close-report 12 "warned by email"

A banned account's sessions end at once; it can't sign in again (no link is
sent, and an earlier link is refused) until it's unbanned. Its drives and
requests stay until they expire; cancel or delete them separately if needed.

`reports` lists the reports users filed that are still open (reports.py),
the oldest first; `close-report` marks one handled, with an optional note.
"""
import argparse
import sys

import auth
import database as db
import reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["ban", "unban", "reports", "close-report"])
    parser.add_argument("target", nargs="?", help="ban, unban: the account's email; close-report: the report's number")
    parser.add_argument("note", nargs="?", help="close-report: what was done")
    args = parser.parse_args(argv)

    db.init_db()
    if args.action == "reports":
        found = reports.open_reports()
        print("\n\n".join(reports.as_text(r) for r in found) if found else "No open reports.")
        return 0
    if not args.target:
        parser.error(f"{args.action} needs {'a report number' if args.action == 'close-report' else 'an email address'}")
    if args.action == "close-report":
        if not args.target.isdigit() or not reports.close(int(args.target), args.note):
            print(f"No open report {args.target}", file=sys.stderr)
            return 1
        print(f"closed: report {args.target}")
        return 0
    email = auth.normalize_email(args.target)
    if not db.set_banned(email, args.action == "ban"):
        print(f"No account with the email {email}", file=sys.stderr)
        return 1
    print(f"{args.action}ned: {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
