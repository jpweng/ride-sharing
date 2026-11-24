"""
A driver is in one place at a time (drive_conflicts.py): no two drives at
the same time, nor two that the driver couldn't get between, in usual
traffic and with their breaks, when posting a drive or accepting a ride
request. Isolated database, straight-line routes at 60 km/h -- no network.
"""
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import database as db
import drive_conflicts
import matching
import pytest
from fastapi import HTTPException
from routes import drives as drive_routes
from routes import ride_requests as request_routes
from routes.match import MatchRequest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _next(weekday: int) -> str:
    """The next day of that weekday (0: Monday), at least two days ahead."""
    day = date.today() + timedelta(days=2)
    return (day + timedelta(days=(weekday - day.weekday()) % 7)).isoformat()


# A Sunday has no peak hours: driving takes 1.1 times the empty road's time all day.
SUNDAY, MONDAY, WEDNESDAY = _next(6), _next(0), _next(2)
DAY = SUNDAY

# In a straight line at 60 km/h: Bern to Lausanne 78 minutes, Lausanne to
# Zürich 2 h 53, Lausanne to Genève 51 minutes, Genève to Zürich 3 h 44.
BERN = {"name": "Bern", "lat": 46.9480, "lon": 7.4474}
LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
ZURICH = {"name": "Zürich", "lat": 47.3769, "lon": 8.5417}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}


@pytest.fixture
def users(user_named, force_haversine, fake_transit):
    return {name.split()[0].lower(): user_named(name) for name in ("Dana D.", "Yann Y.", "Marie M.")}


def _offer(driver, start, end, at, day=DAY):
    return drive_routes.post_drive(drive_routes.DriveCreate(origin=start, destination=end, departure_time=f"{day}T{at}"), driver)


def _refused(driver, start, end, at, day=DAY):
    with pytest.raises(HTTPException) as refused:
        _offer(driver, start, end, at, day)
    assert refused.value.status_code == 409
    return refused.value.detail


def _too_close(driver, start, end, at, day=DAY):
    detail = _refused(driver, start, end, at, day)
    return detail["code"], detail["params"]["time"], detail["params"]["duration"]


def test_not_two_drives_at_the_same_time(users):
    dana = users["dana"]
    first = _offer(dana, BERN, LAUSANNE, "13:00")   # on the road until 14:18
    # The same drive again, one from elsewhere at the same time, one that starts or ends during it.
    for start, end, at in ((BERN, LAUSANNE, "13:00"), (ZURICH, GENEVE, "13:00"), (LAUSANNE, GENEVE, "14:10"), (ZURICH, BERN, "11:30")):
        detail = _refused(dana, start, end, at)
        assert detail["code"] == "drive_overlaps"
        assert detail["params"] == {"from": "Bern", "to": "Lausanne", "time": "13:00", "drive_id": first["id"]}
    assert len(db.list_drives(driver_name="Dana D.", with_geometry=False)) == 1
    # Someone else drives when they like.
    _offer(users["yann"], BERN, LAUSANNE, "13:00")


def test_not_two_drives_too_far_apart_for_the_time_between(users):
    dana = users["dana"]
    first = _offer(dana, BERN, LAUSANNE, "13:00")   # in Lausanne at 14:18 by its route
    # In Zürich at 15:00: 42 minutes. Needed: 8 minutes that traffic adds to the drive, the way
    # of 2 h 53 in traffic (3 h 11), and two breaks for 4 h 37 at the wheel. 3 h 49.
    detail = _refused(dana, ZURICH, BERN, "15:00")
    assert detail["code"] == "drive_too_close"
    assert detail["params"] == {"from": "Bern", "to": "Lausanne", "time": "13:00", "duration": "3 h 49", "drive_id": first["id"]}
    assert "about 3 h 49 are needed" in detail["message"]
    assert _too_close(dana, ZURICH, BERN, "18:00") == ("drive_too_close", "13:00", "3 h 49")   # 7 minutes short
    _offer(dana, ZURICH, BERN, "18:15")
    # And before a drive: in Lausanne at 11:21, then 1 h 46 for Bern (the way of 78 minutes in
    # traffic, 5 minutes added to the drive, a break).
    assert _too_close(dana, GENEVE, LAUSANNE, "10:30") == ("drive_too_close", "13:00", "1 h 46")
    _offer(dana, GENEVE, LAUSANNE, "10:00")


def test_where_one_drive_ends_the_next_starts_a_little_later(users):
    dana = users["dana"]
    _offer(dana, LAUSANNE, GENEVE, "08:00")          # in Genève at 08:51 by its route
    assert _refused(dana, GENEVE, LAUSANNE, "08:40")["code"] == "drive_overlaps"
    # Back at once: only what traffic adds to the 51 minutes.
    assert _too_close(dana, GENEVE, LAUSANNE, "08:55") == ("drive_too_close", "08:00", "5 min")
    _offer(dana, GENEVE, LAUSANNE, "09:00")
    # After 3 h 44 on the road (4 h 07 in traffic): that, and two breaks.
    _offer(dana, GENEVE, ZURICH, "13:00")            # in Zürich at 16:44 by its route
    assert _too_close(dana, ZURICH, BERN, "17:15") == ("drive_too_close", "13:00", "52 min")
    _offer(dana, ZURICH, BERN, "17:45")
    # Another day is far enough for anything.
    _offer(dana, ZURICH, BERN, "08:00", day=MONDAY)


def test_peak_hours_take_longer(users):
    dana, yann = users["dana"], users["yann"]
    # A Wednesday at 08:00, in the morning's peak: 14 minutes more for the 51, not 5.
    _offer(dana, LAUSANNE, GENEVE, "08:00", day=WEDNESDAY)
    assert _too_close(dana, GENEVE, LAUSANNE, "09:00", day=WEDNESDAY) == ("drive_too_close", "08:00", "14 min")
    _offer(dana, GENEVE, LAUSANNE, "09:10", day=WEDNESDAY)
    # The same at 10:00, after it: as on a Sunday.
    _offer(yann, LAUSANNE, GENEVE, "10:00", day=WEDNESDAY)
    _offer(yann, GENEVE, LAUSANNE, "11:00", day=WEDNESDAY)

    sunday, wednesday = datetime.fromisoformat(SUNDAY), datetime.fromisoformat(WEDNESDAY)
    hour = 3600
    assert drive_conflicts.with_traffic(sunday.replace(hour=8), hour) == pytest.approx(1.1 * hour)
    assert drive_conflicts.with_traffic(wednesday.replace(hour=7), hour) == pytest.approx(1.3 * hour)
    assert drive_conflicts.with_traffic(wednesday.replace(hour=17), hour) == pytest.approx(1.3 * hour)
    assert drive_conflicts.with_traffic(wednesday.replace(hour=12), hour) == pytest.approx(1.1 * hour)
    # Leaving at 08:30: half an hour of peak (23 minutes of empty road), the rest after it.
    assert drive_conflicts.with_traffic(wednesday.replace(hour=8, minute=30), hour) == pytest.approx(1800 + (hour - 1800 / 1.3) * 1.1)
    assert drive_conflicts.with_traffic(wednesday.replace(hour=8), 0) == 0


def test_breaks_go_with_the_time_at_the_wheel():
    hour = 3600
    assert [drive_conflicts.breaks_s(h * hour) for h in (0.5, 1.99, 2, 3.9, 4, 6.5)] == [0, 0, 900, 900, 1800, 2700]
    sunday = datetime.fromisoformat(SUNDAY).replace(hour=10)
    # An hour's drive, then an hour's way: 6 minutes more for each in traffic, and one break for 2 h 12.
    assert drive_conflicts.needed_between(sunday, hour, hour) == pytest.approx(0.1 * hour + 1.1 * hour + 900)
    # Half an hour each: no break.
    assert drive_conflicts.needed_between(sunday, hour / 2, hour / 2) == pytest.approx(0.05 * hour + 0.55 * hour)


def test_a_cancelled_drive_is_no_longer_in_the_way(users):
    dana = users["dana"]
    first = _offer(dana, BERN, LAUSANNE, "13:00")
    assert _refused(dana, ZURICH, GENEVE, "13:00")["code"] == "drive_overlaps"
    drive_routes.cancel_drive(first["id"], dana)
    _offer(dana, ZURICH, GENEVE, "13:00")


def test_where_the_driver_is_decides_not_every_drive_they_have(users):
    dana = users["dana"]
    _offer(dana, GENEVE, ZURICH, "06:00")            # in Zürich at 09:44
    _offer(dana, ZURICH, LAUSANNE, "11:00")          # in Lausanne at 13:53
    # From Lausanne, Genève is 51 minutes away; that the day started there doesn't count.
    _offer(dana, GENEVE, LAUSANNE, "16:00")


def test_the_road_is_asked_for_only_when_the_time_could_be_short(users, monkeypatch):
    dana = users["dana"]
    _offer(dana, BERN, LAUSANNE, "06:00")            # in Lausanne at 07:18
    asked = []
    route = matching.route_with_fallback
    monkeypatch.setattr(matching, "route_with_fallback", lambda points: asked.append(points) or route(points))
    start, end = (GENEVE["lat"], GENEVE["lon"]), (LAUSANNE["lat"], LAUSANNE["lon"])
    # From Genève, 51 km from Lausanne, seven hours later: time for it at any speed.
    assert drive_conflicts.conflict("Dana D.", start, end, drive_conflicts._naive(f"{DAY}T14:30"), 3000) is None
    assert asked == []
    # Three hours later: it depends on the road.
    assert drive_conflicts.conflict("Dana D.", start, end, drive_conflicts._naive(f"{DAY}T10:30"), 3000) is None
    assert len(asked) == 1


def test_the_same_drive_sent_twice_at_once_is_posted_once(users):
    dana = users["dana"]
    outcomes = []

    def post():
        try:
            outcomes.append(_offer(dana, BERN, LAUSANNE, "13:00")["id"])
        except HTTPException as refused:
            outcomes.append(refused.detail["code"])

    threads = [threading.Thread(target=post) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(map(str, outcomes))[1:] == ["drive_overlaps"] * 3
    assert len(db.list_drives(driver_name="Dana D.", with_geometry=False)) == 1


def test_a_request_isnt_accepted_by_a_driver_who_is_driving_then(users):
    dana, yann, marie = users["dana"], users["yann"], users["marie"]
    request = request_routes.create_ride_request(MatchRequest(
        origin=BERN, destination=LAUSANNE, requested_time=f"{DAY}T13:00",
    ), marie)["request"]
    _offer(dana, ZURICH, GENEVE, "12:00")            # on the road until 15:44
    with pytest.raises(HTTPException) as refused:
        request_routes.accept_ride_request(request["id"], request_routes.RequestAccept(), dana)
    assert (refused.value.status_code, refused.value.detail["code"]) == (409, "drive_overlaps")
    assert refused.value.detail["params"]["from"] == "Zürich"
    # Nothing was posted for it, and the request is still there for another driver.
    assert len(db.list_drives(driver_name="Dana D.", with_geometry=False)) == 1
    assert db.get_open_request(request["id"])["status"] == "open"
    booking = request_routes.accept_ride_request(request["id"], request_routes.RequestAccept(), yann)
    assert booking["status"] == "accepted"


def test_the_page_names_the_drive_in_the_way():
    # The API's "params" fill the sentence of the error's code.
    assert "t(`errors.${code}`, data.detail.params)" in (FRONTEND / "app.js").read_text()
    for lang in ("en", "de", "fr", "it"):
        errors = (FRONTEND / "i18n" / lang / "errors.json").read_text()
        assert '"errors.drive_overlaps"' in errors and '"errors.drive_too_close"' in errors
