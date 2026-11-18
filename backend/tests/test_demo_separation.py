"""
Demo drives and real use kept apart: real passengers' ride requests never
reach demo drivers and only real drivers are counted; requests made under a
demo driver's name reach demo drivers only; demo drives are never bookable
and come after real ones that fit as well; and the DEMO_DRIVES switch
removes and hides them. Isolated database, offline.
"""
from datetime import date, datetime, timedelta

import database as db
import demo_drives
import main
import maintenance
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
VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
FRIBOURG = {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619}

TOMORROW = date.today() + timedelta(days=1)

DEMO_DRIVER = "Demo Driver"  # drives Aigle -> Bern at 18:00, every day
DEMO_VIEWER = "Demo Viewer"  # another demo driver, someone viewing the demo as them


@pytest.fixture
def demo_network(monkeypatch):
    """Two demo drivers instead of the whole network, with the Aigle -> Bern
    road route stored, as the demo drivers' history needs it."""
    monkeypatch.setattr(demo_drives, "PLACES", {**demo_drives.PLACES, "Aigle": (46.317162, 6.966930)})
    monkeypatch.setattr(demo_drives, "DEMO_DRIVES", [
        demo_drives._drive(DEMO_DRIVER, "Aigle", "Bern", "18:00", demo_drives.DAILY),
        demo_drives._drive(DEMO_VIEWER, "Sion", "Brig", "07:00", demo_drives.MON_FRI),
    ])
    notify._demo_geometries.clear()
    db.save_demo_route("Aigle", "Bern", {"geometry": AIGLE_BERN, "distance_m": 100_000, "duration_s": 6000})


@pytest.fixture
def demo_off(monkeypatch):
    monkeypatch.setenv("DEMO_DRIVES", "off")


def _past_drive(driver, days_ago=7, at="18:00"):
    day = date.today() - timedelta(days=days_ago)
    (olon, olat), (dlon, dlat) = AIGLE_BERN[0], AIGLE_BERN[-1]
    return db.create_drive(
        driver_name=driver, origin_name="Aigle", origin_lat=olat, origin_lon=olon,
        destination_name="Bern", destination_lat=dlat, destination_lon=dlon,
        departure_time=f"{day}T{at}", seats_available=3, route_geometry=AIGLE_BERN,
        route_distance_m=100_000, route_duration_s=6000, route_source="osrm",
    )


def _ask(passenger):
    return main.create_ride_request(main.RideRequestCreate(
        passenger_name=passenger, origin=VEVEY, destination=FRIBOURG, requested_time=f"{TOMORROW}T18:30",
    ))


def _upcoming_drive(driver, is_demo, day=TOMORROW, at="18:00"):
    """An upcoming Vevey -> Fribourg drive (a straight line)."""
    return db.create_drive(
        driver_name=driver, origin_name="Vevey", origin_lat=VEVEY["lat"], origin_lon=VEVEY["lon"],
        destination_name="Fribourg", destination_lat=FRIBOURG["lat"], destination_lon=FRIBOURG["lon"],
        departure_time=f"{day}T{at}", seats_available=3, route_geometry=None,
        route_distance_m=None, route_duration_s=None, route_source="haversine", is_demo=is_demo,
    )


def _match(passenger="Marie"):
    return main.match_ride(main.MatchRequest(
        passenger_name=passenger, origin=VEVEY, destination=FRIBOURG, requested_time=f"{TOMORROW}T18:00",
    ))


# ---------------------------------------------------------------------
# Ride requests
# ---------------------------------------------------------------------
def test_a_real_request_reaches_real_drivers_and_counts_only_them(isolated_db, demo_network):
    _past_drive("Real Driver")

    result = _ask("Marie")

    assert result["drivers_told"] == 1
    assert result["demo_request"] is False
    assert "demo_drivers_told" not in result  # real drivers' names stay private
    assert main.get_notifications("Real Driver")["unread"] == 1
    assert main.get_notifications(DEMO_DRIVER)["notifications"] == []


def test_a_real_request_only_demo_drivers_fit_tells_nobody(isolated_db, demo_network):
    result = _ask("Marie")

    # The passenger is told no driver was asked (ride.askSentNone), not that
    # a fictional one was.
    assert result["drivers_told"] == 0
    assert main.get_notifications(DEMO_DRIVER)["notifications"] == []


def test_the_extra_round_doesnt_reach_demo_drivers_either(isolated_db, demo_network):
    request = _ask("Marie")["request"]
    with db.get_conn() as conn:
        conn.execute("UPDATE open_requests SET created_at = ? WHERE id = ?",
                     ((datetime.now() - timedelta(days=1)).astimezone().isoformat(), request["id"]))

    assert notify.run_extra_rounds() == 0
    assert main.get_notifications(DEMO_DRIVER)["notifications"] == []


def test_a_real_request_skips_drives_posted_under_a_demo_drivers_name(isolated_db, demo_network):
    # Someone viewing the demo as a demo driver posted a drive: still a demo driver.
    _past_drive(DEMO_VIEWER)
    assert _ask("Marie")["drivers_told"] == 0
    assert main.get_notifications(DEMO_VIEWER)["notifications"] == []


def test_a_request_under_a_demo_name_reaches_demo_drivers_only(isolated_db, demo_network):
    _past_drive("Real Driver")

    result = _ask(DEMO_VIEWER)

    assert result["demo_request"] is True
    assert result["drivers_told"] == 1
    assert result["demo_drivers_told"] == [DEMO_DRIVER]
    # Signing in as the demo driver shows it, through the usual rules.
    n = main.get_notifications(DEMO_DRIVER)["notifications"][0]
    assert n["kind"] == "request_for_driver"
    assert n["request"]["passenger_name"] == DEMO_VIEWER
    assert n["details"]["trip"]["times"] > 50
    # No real driver acts on a fictional need.
    assert main.get_notifications("Real Driver")["notifications"] == []


# ---------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------
def test_demo_drives_are_labelled_and_not_bookable(isolated_db, force_haversine, fake_transit):
    demo_id = _upcoming_drive(DEMO_DRIVER, is_demo=True)
    real_id = _upcoming_drive("Real Driver", is_demo=False)

    matches = _match()["driver_matches"]
    assert [(m["id"], m["is_demo"], m["bookable"]) for m in matches] == [
        (real_id, 0, True),  # fits as well: the real one first
        (demo_id, 1, False),
    ]
    assert matches[0]["score"] == matches[1]["score"]

    listed = {d["id"]: d["bookable"] for d in main.get_drives()}
    assert listed == {demo_id: False, real_id: True}
    assert main.get_drive(demo_id)["bookable"] is False
    assert main.get_drive(real_id)["bookable"] is True


def test_rides_just_outside_the_selection_say_whether_bookable(isolated_db, force_haversine, fake_transit):
    _upcoming_drive(DEMO_DRIVER, is_demo=True, at="20:30")  # outside the default time flexibility
    outside = _match()["outside_matches"]
    assert [(m["driver_name"], m["bookable"]) for m in outside] == [(DEMO_DRIVER, False)]


def test_a_better_demo_drive_still_comes_first(isolated_db, force_haversine, fake_transit):
    # Real first only when both fit equally: the ranking itself is unchanged.
    _upcoming_drive(DEMO_DRIVER, is_demo=True, at="18:00")
    _upcoming_drive("Real Driver", is_demo=False, at="18:40")
    assert [m["driver_name"] for m in _match()["driver_matches"]] == [DEMO_DRIVER, "Real Driver"]


# ---------------------------------------------------------------------
# The DEMO_DRIVES switch
# ---------------------------------------------------------------------
@pytest.mark.parametrize("value, on", [
    (None, True), ("", True), ("on", True), ("ON", True), (" on ", True),
    ("off", False), ("Off", False), ("false", False), ("0", False), ("no", False),
])
def test_switch_values(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv("DEMO_DRIVES", raising=False)
    else:
        monkeypatch.setenv("DEMO_DRIVES", value)
    assert demo_drives.enabled() is on


def test_switched_off_demo_drives_are_hidden_everywhere(isolated_db, demo_network, demo_off,
                                                        force_haversine, fake_transit):
    # Before maintenance has deleted them.
    demo_id = _upcoming_drive(DEMO_DRIVER, is_demo=True)
    real_id = _upcoming_drive("Real Driver", is_demo=False)

    assert [d["id"] for d in main.get_drives()] == [real_id]
    with pytest.raises(main.HTTPException) as e:
        main.get_drive(demo_id)
    assert e.value.status_code == 404
    result = _match()
    assert [m["id"] for m in result["driver_matches"] + result["outside_matches"]] == [real_id]

    # A passenger told about it earlier no longer sees it in their inbox.
    request = db.create_open_request("Marie", VEVEY, FRIBOURG, f"{TOMORROW}T18:30", 5, 5, 60)
    db.create_notification("Marie", "drive_for_passenger", request["id"], drive_ids=[demo_id, real_id])
    drives = main.get_notifications("Marie")["notifications"][0]["drives"]
    assert [(d["id"], d["bookable"]) for d in drives] == [(real_id, True)]


def test_switched_off_there_are_no_demo_requests_or_demo_drivers(isolated_db, demo_network, demo_off):
    _past_drive("Real Driver")
    result = _ask(DEMO_VIEWER)  # just a name now
    assert result["demo_request"] is False
    assert result["drivers_told"] == 1
    assert main.get_notifications("Real Driver")["unread"] == 1
    assert main.get_notifications(DEMO_DRIVER)["notifications"] == []


def test_switching_off_deletes_demo_drives_and_routes_then_on_brings_them_back(
    isolated_db, demo_network, force_haversine, monkeypatch,
):
    now = notify.local_now()
    db.save_demo_route("Gone", "Trip", {"geometry": AIGLE_BERN, "distance_m": 1, "duration_s": 1})
    visitor_id = _upcoming_drive("Real Driver", is_demo=False, day=TOMORROW + timedelta(days=1))
    expected = len(demo_drives._expected(now))

    on = maintenance.run(now)
    assert on["demo_created"] == expected
    assert on["demo_routes_deleted"] == 0  # kept while on, even unused ones

    monkeypatch.setenv("DEMO_DRIVES", "off")
    off = maintenance.run(now)
    assert (off["demo_deleted"], off["demo_created"]) == (expected, 0)
    # Aigle -> Bern, used by the deleted drives, and Gone -> Trip, unused.
    # (Sion -> Brig is a straight line here, force_haversine: never stored.)
    assert off["demo_routes_deleted"] == 2
    assert db.all_demo_routes() == {}
    drives = db.list_drives(upcoming_only=False, with_geometry=False)
    assert [d["id"] for d in drives] == [visitor_id]  # visitors' drives untouched
    assert maintenance.run(now)["demo_deleted"] == 0  # nothing left to delete

    monkeypatch.setenv("DEMO_DRIVES", "on")
    assert maintenance.run(now)["demo_created"] == expected


def test_unused_demo_routes_only(isolated_db, force_haversine):
    db.save_demo_route("Lausanne", "Genève", {"geometry": AIGLE_BERN, "distance_m": 1, "duration_s": 1})
    db.save_demo_route("Gone", "Trip", {"geometry": AIGLE_BERN, "distance_m": 1, "duration_s": 1})
    db.create_drive(
        driver_name=DEMO_DRIVER, origin_name="Lausanne", origin_lat=46.5197, origin_lon=6.6323,
        destination_name="Genève", destination_lat=46.2044, destination_lon=6.1432,
        departure_time=f"{TOMORROW}T07:15", seats_available=3, route_geometry=None,
        route_distance_m=1, route_duration_s=1, route_source="osrm", is_demo=True,
    )
    assert db.delete_unused_demo_routes() == 1
    assert set(db.all_demo_routes()) == {("Lausanne", "Genève")}
