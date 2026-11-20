"""
What someone who isn't signed in gets: they can search, and see the rides
with their prices and the drivers' ratings, but not who drives (the name,
and with it the profile, come with signing in); asking for a seat or asking
drivers needs an account. Through the HTTP API, on an isolated database,
offline; and static checks of the page.
"""
import re
from pathlib import Path

import api
from test_auth import FRIBOURG, TOMORROW, VEVEY, _drive, _sign_in, new_client, outbox  # noqa: F401 (fixtures)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
SEARCH = {"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{TOMORROW}T18:00"}


def _a_drive(new_client, outbox):
    driver = new_client()
    _sign_in(driver, outbox, "d@example.ch", "Driver", "One")
    return driver, _drive(driver)


def test_a_search_without_an_account_finds_the_rides_without_their_drivers(new_client, outbox):
    _, drive = _a_drive(new_client, outbox)
    [match] = new_client().post("/api/match", json=SEARCH).json()["driver_matches"]
    assert match["id"] == drive["id"]
    assert (match["driver_name"], match["driver_id"]) == (None, None)
    # What a visitor decides on is all there.
    assert match["bookable"] is True and match["price_chf"] is not None
    assert "driver_rating" in match and match["pickup"]["time"]


def test_signed_in_the_same_search_names_the_driver(new_client, outbox):
    _, drive = _a_drive(new_client, outbox)
    passenger = new_client()
    _sign_in(passenger, outbox, "p@example.ch", "Paula", "Passenger")
    [match] = passenger.post("/api/match", json=SEARCH).json()["driver_matches"]
    assert (match["driver_name"], match["driver_id"]) == ("Driver O.", drive["driver_id"])


def test_listed_drives_name_their_driver_only_to_signed_in_users(new_client, outbox):
    driver, drive = _a_drive(new_client, outbox)
    visitor = new_client()
    assert [d["driver_name"] for d in visitor.get("/api/drives").json()] == [None]
    assert visitor.get(f"/api/drives/{drive['id']}").json()["driver_id"] is None
    assert [d["driver_name"] for d in driver.get("/api/drives").json()] == ["Driver O."]
    assert [d["driver_name"] for d in driver.get("/api/drives?mine=true").json()] == ["Driver O."]
    assert driver.get(f"/api/drives/{drive['id']}").json()["driver_name"] == "Driver O."


def test_a_seat_or_a_request_needs_an_account(new_client, outbox):
    _, drive = _a_drive(new_client, outbox)
    visitor = new_client()
    assert visitor.post(f"/api/drives/{drive['id']}/bookings", json={"seats": 1}).status_code == 401
    assert visitor.post("/api/requests", json=SEARCH).status_code == 401


def test_a_session_without_a_profile_is_not_a_member():
    assert api.is_member(None) is False
    unfinished = {"first_name": None, "adult_confirmed_at": None, "conduct_version": None, "is_demo": 0}
    assert api.is_member({"user": unfinished, "session": {"is_demo": 0}}) is False


def test_the_search_page_is_public_and_the_list_of_rides_is_gone():
    app = (FRONTEND / "app.js").read_text()
    assert re.search(r"ride: \{ needsUser: false,", app)
    html = (FRONTEND / "index.html").read_text()
    for gone in ('href="#/browse"', 'id="page-browse"'):
        assert gone not in html, gone
    assert "loadBrowse" not in app
    # Signed out, the navigation bar leads to the sign-in page.
    assert 'href="#/signin"' in app
    for lang in ("en", "de", "fr", "it"):
        assert not (FRONTEND / "i18n" / lang / "browse.json").exists(), lang
