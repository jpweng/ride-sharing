"""
Ride requests and notifications (notify.py): who is told, the limits on how
often, merging, back-off, the extra round, and telling passengers about new
drives. Runs on an isolated database, offline.
"""
from datetime import date, datetime, timedelta, timezone

import database as db
import demo_drives
import main
import notify
import pytest

AIGLE_BERN = [
    [6.966930, 46.317162],  # Aigle
    [6.923530, 46.394032],  # Villeneuve
    [6.841900, 46.462800],  # Vevey
    [6.900000, 46.527000],  # Châtel-Saint-Denis
    [7.056900, 46.619300],  # Bulle
    [7.161900, 46.806500],  # Fribourg
    [7.447398, 46.940830],  # Bern
]
SION_BRIG = [[7.3606, 46.2331], [7.5354, 46.2919], [7.9876, 46.3159]]

VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
FRIBOURG = {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619}
BULLE = {"name": "Bulle", "lat": 46.6193, "lon": 7.0569}
BERN = {"name": "Bern", "lat": 46.9480, "lon": 7.4474}

TOMORROW = date.today() + timedelta(days=1)
IN_TWO_DAYS = date.today() + timedelta(days=2)


@pytest.fixture(autouse=True)
def no_demo_drivers(monkeypatch):
    # Demo history comes from the templates; tests add their own.
    monkeypatch.setattr(demo_drives, "DEMO_DRIVES", [])
    notify._demo_geometries.clear()


def _past_drive(driver, days_ago, at="18:00", route=AIGLE_BERN):
    day = date.today() - timedelta(days=days_ago)
    (olon, olat), (dlon, dlat) = route[0], route[-1]
    return db.create_drive(
        driver_name=driver, origin_name="A", origin_lat=olat, origin_lon=olon,
        destination_name="B", destination_lat=dlat, destination_lon=dlon,
        departure_time=f"{day}T{at}", seats_available=3, route_geometry=route,
        route_distance_m=100_000, route_duration_s=6000, route_source="osrm",
    )


def _request(passenger="Marie", origin=VEVEY, destination=FRIBOURG, day=TOMORROW, at="18:30"):
    return db.create_open_request(passenger, origin, destination, f"{day}T{at}", 5, 5, 120)


def _age(notification_or_request_table, row_id, hours):
    """Pretend a row was created `hours` ago."""
    created = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with db.get_conn() as conn:
        conn.execute(f"UPDATE {notification_or_request_table} SET created_at = ? WHERE id = ?", (created, row_id))


def _inbox(name):
    return notify.inbox(name)["notifications"]


def test_drivers_who_made_the_trip_are_told_most_frequent_first(isolated_db):
    for days_ago in (7, 14, 21):
        _past_drive("Often", days_ago)
    _past_drive("Once", 7)
    _past_drive("Twice", 7)
    _past_drive("Twice", 14)
    _past_drive("Elsewhere", 7, route=SION_BRIG)
    _past_drive("Marie", 7)  # the passenger themself
    _past_drive("Fourth", 3)

    told = notify.notify_drivers(_request())

    assert told == ["Often", "Twice", "Once"]  # DRIVERS_PER_REQUEST, best first
    n = _inbox("Often")[0]
    assert n["kind"] == "request_for_driver"
    assert n["request"]["passenger_name"] == "Marie"
    assert n["details"]["trip"]["times"] == 3
    # Leave Aigle early enough to be in Vevey at 18:30.
    assert n["details"]["suggested_departure"] < f"{TOMORROW}T18:30"


def test_a_drive_at_another_time_of_day_doesnt_count(isolated_db):
    _past_drive("Morning", 7, at="07:00")
    assert notify.notify_drivers(_request(at="18:30")) == []


def test_a_driver_who_already_posted_a_matching_drive_isnt_told(isolated_db):
    _past_drive("Posted", 7)
    _past_drive("Posted", 7 - 8, at="18:00")  # tomorrow: upcoming, the passenger sees it already
    _past_drive("Other", 7)
    assert notify.notify_drivers(_request()) == ["Other"]


def test_one_notification_a_day_per_driver(isolated_db):
    _past_drive("Busy", 7)
    _past_drive("Busy", 7, at="07:00", route=SION_BRIG)
    _past_drive("Spare", 7)
    assert notify.notify_drivers(_request(), limit=1) == ["Busy"]
    # Another trip, the same day: Busy is over budget, the next one is tried.
    other = _request(passenger="Paul", origin=BULLE, destination=BERN, day=IN_TWO_DAYS)
    assert notify.notify_drivers(other, limit=1) == ["Spare"]


def test_three_notifications_a_week_per_driver(isolated_db):
    _past_drive("Busy", 7)
    for hours in (30, 54, 78):  # one a day on the previous three days
        n = db.create_notification("Busy", "request_for_driver", _request(passenger="X")["id"])
        db.set_notification_action(n["id"], "decline")
        _age("notifications", n["id"], hours)
    assert notify.notify_drivers(_request(day=IN_TWO_DAYS)) == []


def test_same_route_and_day_is_merged_without_using_the_budget(isolated_db):
    _past_drive("Driver", 7)
    first = _request(passenger="Marie")
    second = _request(passenger="Paul", at="18:45")
    assert notify.notify_drivers(first) == ["Driver"]
    assert notify.notify_drivers(second) == ["Driver"]

    inbox = _inbox("Driver")
    assert len(inbox) == 1
    assert inbox[0]["passengers"] == 2


def test_back_off_pauses_after_five_unanswered(isolated_db):
    _past_drive("Silent", 7)
    for i in range(notify.BACKOFF_PAUSE_AFTER):
        n = db.create_notification("Silent", "request_for_driver", _request(passenger=f"P{i}")["id"])
        _age("notifications", n["id"], 24 * (31 + 7 * (notify.BACKOFF_PAUSE_AFTER - i)))
    state = notify.backoff("Silent")
    assert state["paused_until"] is None  # the fifth was 38 days ago: the 30-day pause is over...

    # ...and the next one, unanswered too, pauses them again.
    n = db.create_notification("Silent", "request_for_driver", _request(passenger="Recent")["id"])
    _age("notifications", n["id"], 24 * 8)
    state = notify.backoff("Silent")
    assert state["unanswered"] == notify.BACKOFF_PAUSE_AFTER + 1
    assert state["paused_until"] is not None and not state["can_notify"]
    assert notify.notify_drivers(_request(day=IN_TWO_DAYS)) == []

    # "Turn back on" starts the count again.
    db.update_user_settings("Silent", reset_backoff=True)
    assert notify.backoff("Silent")["can_notify"]


def test_three_unanswered_slow_down_to_one_a_week(isolated_db):
    for i in range(notify.BACKOFF_SLOW_AFTER):
        n = db.create_notification("Quiet", "request_for_driver", _request(passenger=f"P{i}")["id"])
        _age("notifications", n["id"], 24 * 9 + i)
    assert notify.backoff("Quiet")["can_notify"]
    n = db.create_notification("Quiet", "request_for_driver", _request(passenger="P")["id"])
    _age("notifications", n["id"], 24 * 3)
    assert not notify.backoff("Quiet")["can_notify"]  # one in the last week already
    db.set_notification_action(n["id"], "decline")  # any answer resets the count
    assert notify.backoff("Quiet")["can_notify"]


def test_muted_route_and_notifications_off(isolated_db):
    _past_drive("Muted", 7)
    _past_drive("Off", 7)
    db.add_route_mute("Muted", (VEVEY["lat"], VEVEY["lon"]), (FRIBOURG["lat"], FRIBOURG["lon"]))
    db.update_user_settings("Off", notify_requests=False)
    assert notify.notify_drivers(_request()) == []


def test_real_drivers_before_demo_drivers(isolated_db, monkeypatch):
    db.save_demo_route("Aigle", "Bern", {"geometry": AIGLE_BERN, "distance_m": 100_000, "duration_s": 6000})
    monkeypatch.setattr(demo_drives, "PLACES", {**demo_drives.PLACES, "Aigle": (46.317162, 6.966930)})
    template = demo_drives._drive("Demo Driver", "Aigle", "Bern", "18:00", demo_drives.DAILY)
    monkeypatch.setattr(demo_drives, "DEMO_DRIVES", [template])
    _past_drive("Real Driver", 7)

    # The demo driver drove it ~80 times, the real one once: the real one comes first.
    assert notify.notify_drivers(_request(), limit=2) == ["Real Driver", "Demo Driver"]
    assert _inbox("Demo Driver")[0]["details"]["trip"]["times"] > 50


def test_posting_a_drive_tells_waiting_passengers_once_a_day(isolated_db, force_haversine, fake_transit):
    request = _request(day=IN_TWO_DAYS)

    def post(driver, at):
        # A straight line (force_haversine), from Vevey to Fribourg.
        return main.post_drive(main.DriveCreate(
            driver_name=driver, origin=VEVEY, destination=FRIBOURG, departure_time=f"{IN_TWO_DAYS}T{at}",
        ))

    post("First", "18:00")
    post("Second", "18:15")
    post("Wrong time", "06:00")

    inbox = _inbox("Marie")
    assert len(inbox) == 1
    assert inbox[0]["kind"] == "drive_for_passenger"
    assert inbox[0]["request"]["id"] == request["id"]
    assert [d["driver_name"] for d in inbox[0]["drives"]] == ["First", "Second"]


def test_extra_round_once_after_twelve_hours_without_an_offer(isolated_db):
    for i in range(5):
        _past_drive(f"Driver {i}", 7 + i)
        _past_drive(f"Driver {i}", 14 + i)  # equal counts: ties don't matter here
    request = _request(day=IN_TWO_DAYS)
    first = notify.notify_drivers(request)
    assert len(first) == notify.DRIVERS_PER_REQUEST

    assert notify.run_extra_rounds() == 0  # too early
    _age("open_requests", request["id"], 13)
    assert notify.run_extra_rounds() == notify.EXTRA_ROUND_DRIVERS
    assert notify.run_extra_rounds() == 0  # only once


def test_api_request_flow(isolated_db, force_haversine, fake_transit):
    _past_drive("Driver", 7)
    payload = dict(
        passenger_name="Marie",
        origin=VEVEY, destination=FRIBOURG,
        requested_time=f"{TOMORROW}T18:30",
    )
    result = main.create_ride_request(main.RideRequestCreate(**payload))
    assert result["drivers_told"] == 1
    assert [r["id"] for r in main.get_ride_requests_open("Marie")] == [result["request"]["id"]]

    inbox = main.get_notifications("Driver")
    assert inbox["unread"] == 1
    n = inbox["notifications"][0]
    main.act_on_notification(n["id"], main.NotificationAction(name="Driver", action="mute"))
    assert db.list_route_mutes("Driver")
    main.read_notifications("Driver")
    assert main.get_notifications("Driver")["unread"] == 0

    main.cancel_ride_request(result["request"]["id"], "Marie")
    assert main.get_ride_requests_open("Marie") == []


@pytest.mark.parametrize("when, code", [
    (timedelta(minutes=30), "request_too_soon"),
    (timedelta(days=15), "request_too_far"),
])
def test_api_request_time_limits(isolated_db, when, code):
    asked = (notify.local_now() + when).isoformat(timespec="minutes")
    with pytest.raises(main.HTTPException) as e:
        main.create_ride_request(main.RideRequestCreate(
            passenger_name="Marie", origin=VEVEY, destination=FRIBOURG, requested_time=asked,
        ))
    assert e.value.detail["code"] == code
