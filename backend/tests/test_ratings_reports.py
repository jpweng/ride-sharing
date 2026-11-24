"""
Ratings after a ride (ratings.py) and reports of a problem (reports.py):
who can rate or report whom and when, what is shown, the request to rate
once a ride is over, and what the team sees. Isolated temp DB and
haversine routing -- no network.
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import admin
import database as db
import mailer
import main
import maintenance
import notify
import ratings
import reports
from routes import bookings as booking_routes
from routes import drives as drive_routes
from routes import match as match_routes
from routes import ratings as rating_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
DEPARTURE = datetime.fromisoformat(f"{TOMORROW}T08:00")
AFTER = DEPARTURE + timedelta(hours=3)   # the ride is over

LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}


@pytest.fixture
def users(user_named, force_haversine):
    return {name.split()[0].lower(): user_named(name) for name in ("Jean D.", "Marie M.", "Paul P.", "Lea L.")}


@pytest.fixture
def later(monkeypatch):
    """Make it AFTER the ride."""
    def move():
        monkeypatch.setattr(notify, "local_now", lambda: AFTER)
    return move


def _ride(driver, passenger, at="08:00", accept=True):
    """A drive with the passenger booked on it; the booking."""
    drive = drive_routes.post_drive(drive_routes.DriveCreate(
        origin=LAUSANNE, destination=GENEVE, departure_time=f"{TOMORROW}T{at}", seats_available=3,
    ), driver)
    booking = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(), passenger)
    if accept:
        booking = booking_routes.accept_booking(booking["id"], driver, booking_routes.BookingAnswer())
    return booking


def _rate(user, booking, stars):
    return rating_routes.rate_ride(booking["id"], rating_routes.Rating(stars=stars), user)


def _report(user, booking, reason="no_show", details=None):
    return rating_routes.report_ride(booking["id"], rating_routes.Report(reason=reason, details=details), user)


def _refused(call, *args):
    with pytest.raises(HTTPException) as exc:
        call(*args)
    return exc.value.status_code, exc.value.detail["code"]


def _kinds(user):
    return [n["kind"] for n in reversed(notify.inbox(user["name"])["notifications"])]


# --- Rating ----------------------------------------------------------------------

def test_each_side_rates_the_other_once_the_ride_is_over(users, later):
    jean, marie = users["jean"], users["marie"]
    booking = _ride(jean, marie)
    assert _refused(_rate, marie, booking, 5) == (400, "ride_not_over")
    later()
    assert _rate(marie, booking, 5) == {"rated_role": "driver", "stars": 5}
    assert _rate(jean, booking, 4) == {"rated_role": "passenger", "stars": 4}
    # Once each.
    assert _refused(_rate, marie, booking, 1) == (400, "already_rated")
    assert ratings.summary(["Jean D.", "Marie M."], "driver") == {"Jean D.": {"average": 5.0, "count": 1}}
    assert ratings.summary(["Jean D.", "Marie M."], "passenger") == {"Marie M.": {"average": 4.0, "count": 1}}


def test_who_cant_rate(users, later):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    booked = _ride(jean, marie)
    only_asked = _ride(jean, paul, at="12:00", accept=False)
    later()
    assert _refused(_rate, paul, booked, 5) == (404, "booking_not_found")       # not their ride
    assert _refused(_rate, paul, only_asked, 5) == (400, "not_rateable")       # never accepted
    assert _refused(rating_routes.rate_ride, 999, rating_routes.Rating(stars=5), marie) == (404, "booking_not_found")
    for stars in (0, 6):
        with pytest.raises(ValidationError):
            rating_routes.Rating(stars=stars)


def test_ratings_show_on_drives_searches_and_bookings(users, later, fake_transit, monkeypatch):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    first = _ride(jean, marie)
    later()
    _rate(marie, first, 4)
    _rate(jean, first, 5)
    monkeypatch.setattr(notify, "local_now", lambda: DEPARTURE - timedelta(hours=20))

    # A later drive of Jean's shows what he was rated as a driver...
    second = _ride(jean, paul, at="18:00", accept=False)
    assert drive_routes.get_drive(second["drive_id"])["driver_rating"] == {"average": 4.0, "count": 1}
    listed = {d["id"]: d for d in drive_routes.get_drives(None)}
    assert listed[second["drive_id"]]["driver_rating"] == {"average": 4.0, "count": 1}
    search = match_routes.MatchRequest(origin=LAUSANNE, destination=GENEVE, requested_time=f"{TOMORROW}T18:00")
    assert {m["driver_rating"]["average"] for m in match_routes.match_ride(search)["driver_matches"]} == {4.0}
    # ...Paul sees it on his booking, and Jean sees nothing yet about Paul.
    [pauls] = booking_routes.get_bookings(paul)["as_passenger"]
    assert pauls["drive"]["driver_rating"] == {"average": 4.0, "count": 1}
    assert pauls["passenger_rating"] is None
    # A passenger rated before shows it to the next driver.
    lea = users["lea"]
    again = _ride(lea, marie, at="20:00", accept=False)
    [request] = [b for b in booking_routes.get_bookings(lea)["as_driver"] if b["id"] == again["id"]]
    assert request["passenger_rating"] == {"average": 5.0, "count": 1}
    assert request["drive"]["driver_rating"] is None  # Lea has never been rated as a driver


def test_the_average_is_to_one_decimal(users, later):
    jean = users["jean"]
    rides = [_ride(jean, users[name], at=at) for at, name in (("02:00", "marie"), ("05:00", "paul"), ("08:00", "lea"))]
    later()
    for name, booking, stars in zip(("marie", "paul", "lea"), rides, (5, 4, 4)):
        _rate(users[name], booking, stars)
    assert ratings.summary(["Jean D."], "driver") == {"Jean D.": {"average": 4.3, "count": 3}}


# --- Asking for a rating -----------------------------------------------------------

def test_both_sides_are_asked_once_when_the_ride_is_over(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    booked = _ride(jean, marie)
    _ride(jean, paul, at="12:00", accept=False)   # never accepted: nobody is asked
    before = len(_kinds(jean))

    assert ratings.ask_after_rides(DEPARTURE) == 0   # not over yet
    assert ratings.ask_after_rides(AFTER) == 1
    assert _kinds(marie)[-1] == "rate_ride" and _kinds(jean)[-1] == "rate_ride"
    assert len(_kinds(jean)) == before + 1
    asked = notify.inbox("Marie M.")["notifications"][0]["details"]
    assert (asked["booking_id"], asked["rated_role"], asked["rated_name"]) == (booked["id"], "driver", "Jean D.")
    assert asked["drive"]["destination_name"] == "Genève"
    asked = notify.inbox("Jean D.")["notifications"][0]["details"]
    assert (asked["rated_role"], asked["rated_name"]) == ("passenger", "Marie M.")
    # Once.
    assert ratings.ask_after_rides(AFTER + timedelta(hours=5)) == 0
    assert "rate_ride" not in _kinds(paul)


def test_maintenance_asks(users):
    _ride(users["jean"], users["marie"])
    assert maintenance.run(AFTER)["rides_to_rate"] == 1


# --- When accounts and drives go ---------------------------------------------------

def _account(name, email):
    """An account (with an email address): unlike a bare name, it isn't
    deleted once nothing references it."""
    import auth
    user = db.create_account(email, f"~{name}", "en")
    first, _, last = name.partition(" ")
    return db.set_profile(user["id"], auth.CONDUCT_VERSION, name, first, last)


def test_ratings_outlive_the_ride_and_follow_the_accounts(isolated_db, force_haversine, later):
    jean, marie = _account("Jean D.", "jean@example.ch"), _account("Marie M.", "marie@example.ch")
    booking = _ride(jean, marie)
    later()
    _rate(marie, booking, 5)
    _rate(jean, booking, 3)

    # The drive and its booking are purged a month later: the ratings stay.
    maintenance.purge_old_data(DEPARTURE + maintenance.RETENTION + timedelta(days=1))
    assert ratings.summary(["Jean D."], "driver") == {"Jean D.": {"average": 5.0, "count": 1}}

    # Marie deletes her account: what she was rated goes, what she gave stays.
    db.delete_account(marie["id"])
    assert ratings.summary(["Marie M."], "passenger") == {}
    assert ratings.summary(["Jean D."], "driver") == {"Jean D.": {"average": 5.0, "count": 1}}
    with db.get_conn() as conn:
        assert [tuple(r) for r in conn.execute("SELECT rater_id, rated_id FROM ratings")] == [(None, jean["id"])]


# --- Reporting -------------------------------------------------------------------

def test_a_report_keeps_the_ride_as_it_was_and_stays_between_reporter_and_team(users):
    jean, marie = users["jean"], users["marie"]
    booking = _ride(jean, marie)
    assert _report(marie, booking, "no_show", "  Waited 20 minutes at the station.  ") == {"id": 1, "reason": "no_show"}
    # The driver isn't told.
    assert "report" not in " ".join(_kinds(jean))

    [report] = reports.open_reports()
    assert (report["reporter_name"], report["reported_name"]) == ("Marie M.", "Jean D.")
    assert report["details"] == "Waited 20 minutes at the station."
    assert report["context"]["reported_as"] == "driver"
    assert report["context"]["drive"] == "Lausanne → Genève"
    text = reports.as_text(report)
    assert "Marie M. reports Jean D. (the driver)" in text and "Waited 20 minutes" in text

    # Once per ride and reporter; the driver can report the passenger too.
    assert _refused(_report, marie, booking) == (400, "already_reported")
    assert _report(jean, booking, "behaviour")["id"] == 2
    assert [r["context"]["reported_as"] for r in reports.open_reports()] == ["driver", "passenger"]


def test_who_can_report(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    only_asked = _ride(jean, marie, accept=False)
    assert _refused(_report, paul, only_asked) == (404, "booking_not_found")
    # Before the ride, and without it ever being accepted: a problem is a problem.
    assert _report(marie, only_asked, "other")["reason"] == "other"
    with pytest.raises(ValidationError):
        rating_routes.Report(reason="made_up")
    with pytest.raises(ValidationError):
        rating_routes.Report(reason="late", details="x" * 1001)


def test_reports_stay_when_the_ride_and_the_accounts_go(users):
    jean, marie = users["jean"], users["marie"]
    _report(marie, _ride(jean, marie), "unsafe")
    maintenance.purge_old_data(DEPARTURE + maintenance.RETENTION + timedelta(days=1))
    db.delete_account(marie["id"])
    db.delete_account(jean["id"])
    [report] = reports.open_reports()
    assert (report["reporter_id"], report["reported_id"]) == (None, None)
    assert (report["reporter_name"], report["reported_name"]) == ("Marie M.", "Jean D.")


def test_the_team_is_emailed_when_an_address_is_set(users, monkeypatch):
    outbox = mailer.ConsoleBackend()
    monkeypatch.setattr(mailer, "_backend", outbox)
    jean, marie = users["jean"], users["marie"]
    booking = _ride(jean, marie)
    _report(jean, booking, "late")
    assert outbox.outbox == []   # no address: logged only
    monkeypatch.setenv("REPORTS_EMAIL", "team@rideandrail.example")
    _report(marie, booking, "unsafe", "<b>Drove through a red light</b>")
    [email] = outbox.outbox
    assert email["to"] == "team@rideandrail.example"
    assert "unsafe" in email["subject"] and "red light" in email["text"]
    assert "<b>" not in email["html"]

    # A failing email doesn't lose the report.
    def broken(*args):
        raise mailer.EmailError("no")
    monkeypatch.setattr(outbox, "send", broken)
    assert _report(marie, _ride(jean, marie, at="10:00"), "late")["reason"] == "late"


def test_the_admin_tool_lists_and_closes_reports(users, capsys):
    jean, marie = users["jean"], users["marie"]
    assert admin.main(["reports"]) == 0
    assert "No open reports." in capsys.readouterr().out
    _report(marie, _ride(jean, marie), "no_show", "Never came")
    assert admin.main(["reports"]) == 0
    out = capsys.readouterr().out
    assert "Report #1" in out and "Never came" in out
    assert admin.main(["close-report", "1", "warned"]) == 0
    assert reports.open_reports() == []
    assert reports.get(1)["closing_note"] == "warned"
    assert admin.main(["close-report", "1"]) == 1
    assert admin.main(["close-report", "abc"]) == 1


@pytest.mark.parametrize("path", ["/api/bookings/1/rating", "/api/bookings/1/report"])
def test_rating_and_reporting_need_a_session(isolated_db, path):
    resp = TestClient(main.app, base_url="https://testserver").post(path, json={"stars": 5, "reason": "late"})
    assert resp.status_code == 401
