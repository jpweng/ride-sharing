"""
Admin tasks without an admin UI. Run on the server (Session Manager shell):

    docker compose exec app python backend/admin.py ban someone@example.ch
    docker compose exec app python backend/admin.py unban someone@example.ch
    docker compose exec app python backend/admin.py reports
    docker compose exec app python backend/admin.py close-report 12 "warned by email"
    docker compose exec app python backend/admin.py payments
    docker compose exec app python backend/admin.py refund-payment 7
    docker compose exec app python backend/admin.py release-payment 7

A banned account's sessions end at once; it can't sign in again (no link is
sent, and an earlier link is refused) until it's unbanned. Its drives and
requests stay until they expire; cancel or delete them separately if needed.

`reports` lists the reports users filed that are still open (reports.py),
the oldest first; `close-report` marks one handled, with an optional note.

`payments` lists the payments on hold because their passenger said the
ride didn't take place (payments.py; each also filed a report).
`refund-payment` gives one back to the passenger in full;
`release-payment` lets it go to the driver, at the next hourly run. Both
sides are told the outcome.
"""
import argparse
import sys

import auth
import database as db
import payments
import reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=[
        "ban", "unban", "reports", "close-report", "payments", "refund-payment", "release-payment",
    ])
    parser.add_argument("target", nargs="?", help="ban, unban: the account's email; close-report: the report's"
                                                  " number; refund-payment, release-payment: the payment's number")
    parser.add_argument("note", nargs="?", help="close-report: what was done")
    args = parser.parse_args(argv)

    db.init_db()
    if args.action == "reports":
        found = reports.open_reports()
        print("\n\n".join(reports.as_text(r) for r in found) if found else "No open reports.")
        return 0
    if args.action == "payments":
        held = payments.on_hold()
        print("\n\n".join(payments.as_text(p) for p in held) if held else "No payments on hold.")
        return 0
    if args.action in ("refund-payment", "release-payment"):
        refund = args.action == "refund-payment"
        if not (args.target or "").isdigit() or not payments.resolve_dispute(int(args.target), refund=refund):
            print(f"No payment {args.target} on hold", file=sys.stderr)
            return 1
        print(f"{'refunded' if refund else 'released'}: payment {args.target}")
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
