"""
Paying drivers (payouts.py, routes/payouts.py, docs/payments.md): setting
payouts up on Stripe's pages, what the passengers of a finished ride paid
being sent to the driver, the money waiting for a driver who hasn't set
payouts up, and what happens to it in the end. Stripe is a fake
(fake_stripe.py); isolated temp DB and haversine routing.
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

import database as db
import main
import maintenance
import notify
import notify_email
import payments
import payouts
import translations
from fake_stripe import NOW, at_noon, stripe  # noqa: F401 (fixtures)
from routes import auth as auth_routes
from routes import bookings as booking_routes
from routes import drives as drive_routes

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
LANGS = ["en", "de", "fr", "it"]
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
BASE = "https://rideandrail.test"

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}


@pytest.fixture
def users(user_named, force_haversine):
    """Jean drives; Marie and Paul ride."""
    return {name.split()[0].lower(): user_named(name) for name in ("Jean D.", "Marie M.", "Paul P.")}


def _paid_ride(users, stripe, passenger="marie", drive=None):
    """A booking of the passenger's on a drive of Jean's, accepted and paid."""
    drive = drive or drive_routes.post_drive(drive_routes.DriveCreate(
        origin=LAUSANNE, destination=GENEVE, departure_time=f"{TOMORROW}T08:00", seats_available=3,
    ), users["jean"])
    asked = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(), users[passenger])
    booking = booking_routes.accept_booking(asked["id"], users["jean"])
    payments.checkout(booking["id"], users[passenger], BASE)
    payments._apply_checkout(stripe.pay(stripe.last()))
    return booking


def _set_up(users, stripe, driver="jean"):
    """The driver went through Stripe's pages and was accepted."""
    payouts.onboard(users[driver], BASE)
    account_id = list(stripe.accounts)[-1]
    stripe.approve(account_id)
    payouts.refresh(users[driver])
    return account_id


def _after_ride(booking, **more):
    """A moment that long after the booking's drop-off."""
    return datetime.fromisoformat(booking["dropoff"]["time"]) + timedelta(**more)


def _refused(call, *args, **kwargs):
    with pytest.raises(HTTPException) as exc:
        call(*args, **kwargs)
    return exc.value.status_code, exc.value.detail["code"]


def _inbox(user):
    return [(n["kind"], n["details"]) for n in reversed(notify.inbox(user["name"])["notifications"])]


def _kinds(user):
    return [kind for kind, _ in _inbox(user)]


# --- Setting payouts up -----------------------------------------------------

def test_off_with_payments(users):
    assert payouts.status(users["jean"]) == {"enabled": False, "state": "none", "waiting_chf": 0.0, "sent_chf": 0.0}
    assert _refused(payouts.onboard, users["jean"], BASE) == (503, "payments_off")
    assert _refused(payouts.dashboard, users["jean"]) == (503, "payments_off")
    assert payouts.run(NOW) == {"payouts_sent": 0, "payouts_given_back": 0, "payout_reminders": 0}


def test_setting_up_on_stripes_pages(users, stripe):
    jean = users["jean"]
    assert payouts.status(jean)["state"] == "none"

    url = payouts.onboard(jean, BASE)

    assert url == "https://connect.test/acct_1/setup"
    asked = stripe.accounts["acct_1"]["asked"]
    assert asked["metadata"] == {"user_id": str(jean["id"])}
    assert stripe.links == [("acct_1", f"{BASE}/?payouts=done#/drive", f"{BASE}/?payouts=again#/drive")]
    assert payouts.status(jean)["state"] == "incomplete"
    # Carrying on later: the same account, a new link.
    payouts.onboard(jean, BASE)
    assert list(stripe.accounts) == ["acct_1"] and len(stripe.links) == 2
    # Back from Stripe's pages, before and after Stripe accepted them.
    assert payouts.refresh(jean)["state"] == "incomplete"
    stripe.approve("acct_1")
    assert payouts.refresh(jean)["state"] == "ready"
    assert payouts.status(jean)["state"] == "ready"


def test_the_drivers_own_pages_at_stripe(users, stripe):
    assert _refused(payouts.dashboard, users["jean"]) == (400, "payouts_not_set_up")
    payouts.onboard(users["jean"], BASE)
    assert _refused(payouts.dashboard, users["jean"]) == (400, "payouts_not_set_up")  # not through the pages yet
    _set_up(users, stripe)
    assert payouts.dashboard(users["jean"]) == "https://connect.test/acct_1"


def test_stripe_failing_says_so(users, stripe):
    stripe.failing.add("create_account")
    assert _refused(payouts.onboard, users["jean"], BASE) == (502, "payout_failed")
    assert payouts.status(users["jean"])["state"] == "none"


def test_the_routes_need_a_session():
    client = TestClient(main.app)
    assert client.get("/api/payouts").status_code == 401
    for path in ("onboard", "refresh", "dashboard"):
        assert client.post(f"/api/payouts/{path}").status_code == 401


# --- Sending the money --------------------------------------------------------

def test_a_day_after_the_ride_the_driver_is_sent_what_was_paid(users, stripe):
    account = _set_up(users, stripe)
    booking = _paid_ride(users, stripe)
    paid = payments.for_booking(booking["id"])["amount_cts"]
    assert payouts.status(users["jean"])["waiting_chf"] == paid / 100

    # Not before the ride, nor right after it.
    assert payouts.pay_out(NOW) == {"sent": 0, "given_back": 0}
    assert payouts.pay_out(_after_ride(booking, hours=23)) == {"sent": 0, "given_back": 0}
    assert payouts.pay_out(_after_ride(booking, hours=24)) == {"sent": 1, "given_back": 0}

    # All of it, tied to the passenger's payment.
    assert stripe.transfers == [(account, paid, "ch_1")]
    payment = payments.for_booking(booking["id"])
    assert (payment["transfer_id"], payment["transferred_cts"]) == ("tr_1", paid)
    kind, details = _inbox(users["jean"])[-1]
    assert (kind, details["amount_chf"], details["drive"]["origin_name"]) == ("payout_sent", paid / 100, "Lausanne")
    assert payouts.status(users["jean"]) == {"enabled": True, "state": "ready", "waiting_chf": 0.0, "sent_chf": paid / 100}
    # Once.
    assert payouts.pay_out(_after_ride(booking, days=3)) == {"sent": 0, "given_back": 0}
    assert len(stripe.transfers) == 1


def test_every_passenger_of_the_ride(users, stripe):
    _set_up(users, stripe)
    marie = _paid_ride(users, stripe, "marie")
    _paid_ride(users, stripe, "paul", drive=drive_routes.get_drive(marie["drive_id"]))
    assert maintenance.run(_after_ride(marie, days=1))["payouts_sent"] == 2
    assert [charge for _, _, charge in stripe.transfers] == ["ch_1", "ch_2"]


def test_a_refunded_booking_sends_nothing(users, stripe):
    _set_up(users, stripe)
    booking = _paid_ride(users, stripe)
    booking_routes.cancel_booking(booking["id"], users["marie"])
    assert payouts.status(users["jean"])["waiting_chf"] == 0.0
    assert payouts.pay_out(_after_ride(booking, days=2)) == {"sent": 0, "given_back": 0}
    assert stripe.transfers == []


def test_a_transfer_that_fails_is_tried_again(users, stripe):
    _set_up(users, stripe)
    booking = _paid_ride(users, stripe)
    stripe.failing.add("transfer")
    assert payouts.pay_out(_after_ride(booking, days=1)) == {"sent": 0, "given_back": 0}
    assert "payout_sent" not in _kinds(users["jean"])
    stripe.failing.clear()
    assert payouts.pay_out(_after_ride(booking, days=1, hours=1)) == {"sent": 1, "given_back": 0}


# --- A driver who hasn't set payouts up --------------------------------------

def test_the_money_waits_and_the_driver_is_reminded(users, stripe):
    booking = _paid_ride(users, stripe)
    paid = payments.for_booking(booking["id"])["amount_cts"]

    # Told within the hour that there is money to receive, and how.
    assert payouts.run(NOW)["payout_reminders"] == 1
    kind, details = _inbox(users["jean"])[-1]
    assert (kind, details["amount_chf"]) == ("payout_setup", paid / 100)
    # Not every hour.
    assert payouts.run(NOW)["payout_reminders"] == 0

    # The ride is over: nothing can be sent yet, and nothing is lost.
    assert payouts.run(_after_ride(booking, days=2)) == {"payouts_sent": 0, "payouts_given_back": 0, "payout_reminders": 0}
    assert payouts.status(users["jean"])["waiting_chf"] == paid / 100

    # Stripe is asked again at each run: once the account is accepted, the money goes.
    payouts.onboard(users["jean"], BASE)
    assert payouts.pay_out(_after_ride(booking, days=3)) == {"sent": 0, "given_back": 0}
    stripe.approve("acct_1")
    assert payouts.pay_out(_after_ride(booking, days=3)) == {"sent": 1, "given_back": 0}
    assert stripe.transfers == [("acct_1", paid, "ch_1")]
    assert payouts.remind(NOW) == 0  # nothing waiting any more


def test_reminded_again_a_week_later(users, stripe):
    _paid_ride(users, stripe)
    assert payouts.remind(NOW) == 1
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE notifications SET created_at = ? WHERE kind = 'payout_setup'",
            ((datetime.fromisoformat(db.now_iso()) - payouts.REMIND_EVERY - timedelta(hours=1)).isoformat(),),
        )
    assert payouts.remind(NOW) == 1


def test_money_nobody_claimed_goes_back_to_the_passenger(users, stripe):
    booking = _paid_ride(users, stripe)
    paid = payments.for_booking(booking["id"])["amount_cts"]
    before = _after_ride(booking) + payouts.UNCLAIMED_AFTER - timedelta(hours=1)

    assert payouts.pay_out(before) == {"sent": 0, "given_back": 0}
    assert payouts.pay_out(before + timedelta(hours=1)) == {"sent": 0, "given_back": 1}

    assert stripe.refunds == [("pi_1", paid)] and stripe.transfers == []
    assert _kinds(users["jean"])[-1] == "payout_lapsed"
    assert _kinds(users["marie"])[-1] == "payment_refunded"
    assert payouts.status(users["jean"])["waiting_chf"] == 0.0
    assert payouts.pay_out(before + timedelta(days=1)) == {"sent": 0, "given_back": 0}


# --- When the driver's account goes -------------------------------------------

def _delete_account(user):
    return auth_routes.delete_account(Response(), {"user": db.get_user(user["id"]), "session": {"is_demo": False}})


def test_a_driver_deleting_their_account_is_paid_first(users, stripe, monkeypatch):
    account = _set_up(users, stripe)
    booking = _paid_ride(users, stripe)
    paid = payments.for_booking(booking["id"])["amount_cts"]
    # An hour after the ride: the usual day's wait isn't over.
    monkeypatch.setattr(notify, "local_now", lambda: _after_ride(booking, hours=1))

    _delete_account(users["jean"])

    assert stripe.transfers == [(account, paid, "ch_1")] and stripe.refunds == []
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payout_accounts").fetchone()[0] == 0


def test_a_driver_gone_without_payouts_set_up(users, stripe, monkeypatch):
    booking = _paid_ride(users, stripe)
    paid = payments.for_booking(booking["id"])["amount_cts"]
    monkeypatch.setattr(notify, "local_now", lambda: _after_ride(booking, hours=1))
    _delete_account(users["jean"])
    assert stripe.transfers == [] and stripe.refunds == []

    # The money can't reach them any more: back to the passenger.
    assert payouts.pay_out(_after_ride(booking, days=1)) == {"sent": 0, "given_back": 1}
    assert stripe.refunds == [("pi_1", paid)]


# --- What users read ----------------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_the_emails_about_payouts(lang, isolated_db):
    drive = {"origin_name": "Lausanne", "destination_name": "Genève", "departure_time": f"{TOMORROW}T08:00"}
    other = translations.dictionary(lang)["email.notify.other"]
    for kind, amount in (("payout_setup", "12.50"), ("payout_sent", "12.50"), ("payout_lapsed", None)):
        n = {"kind": kind, "details": {"amount_chf": 12.5, "drive": drive}, "request_id": 0}
        sentence, trip = notify_email._sentence(n, lang)
        assert sentence != other and "Lausanne → Genève" in trip
        assert amount is None or amount in sentence


def test_the_page_loads_the_script_after_payments():
    html = (FRONTEND / "index.html").read_text()
    assert html.index('<script src="payments.js"></script>') < html.index('<script src="payouts.js"></script>')
