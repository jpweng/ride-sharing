"""
Tests for events (events.py, events.json): the committed file is valid,
validation catches the mistakes a pull request could make, opening hours
and the suggested late departures back, the API, the shareable /e/<slug>
link, and the demo drives on the events' days. No network.
"""
import copy
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException

import demo_drives
import events
import main
import matching

with open(events.EVENTS_PATH, encoding="utf-8") as f:
    RAW = json.load(f)


def _valid_event(**changes):
    event = {
        "slug": "test-fair-2026",
        "name": {"de": "Messe", "fr": "Foire", "it": "Fiera", "en": "Fair"},
        "place": {"name": "Fair grounds", "lat": 46.5, "lon": 6.6, "station": "Lausanne"},
        "first_day": "2026-12-01",
        "last_day": "2026-12-07",
        "hours": {day: {"open": "10:00", "close": "20:00"} for day in events.WEEKDAYS},
        "verified": True,
    }
    event.update(changes)
    return event


def _problems(*evts):
    with pytest.raises(events.EventDataError) as e:
        events.validate({"events": list(evts)})
    return str(e.value)


# --- The committed file -------------------------------------------------------

def test_committed_file_is_valid():
    assert events.validate(copy.deepcopy(RAW)) == RAW["events"]
    assert {"montreux-noel-2026", "bo-noel-lausanne-2026", "basel-christmas-market-2026"} <= set(events.BY_SLUG)


def test_unverified_data_says_what_to_check():
    for event in RAW["events"]:
        unverified = not event["verified"] or any(
            isinstance(h, dict) and h.get("verified") is False for h in event.get("exceptions", {}).values()
        )
        if unverified:
            assert event.get("todo"), event["slug"]


# --- Validation ---------------------------------------------------------------

def test_a_valid_event_passes():
    assert events.validate({"events": [_valid_event()]})[0]["slug"] == "test-fair-2026"


@pytest.mark.parametrize("changes, message", [
    ({"slug": "Test Fair"}, "slug"),
    ({"name": {"de": "Messe", "fr": "Foire", "en": "Fair"}}, "name"),
    ({"place": {"name": "Paris", "lat": 48.85, "lon": 2.35, "station": "Paris"}}, "place.lat"),
    ({"first_day": "2026-12-08"}, "last_day is before first_day"),
    ({"last_day": "7 Dec 2026"}, "last_day"),
    ({"hours": {"mon": {"open": "10:00", "close": "20:00"}}}, "hours"),
    ({"exceptions": {"2026-12-01": {"open": "25:00", "close": "20:00"}}}, "HH:MM"),
    ({"exceptions": {"2026-12-24": None}}, "outside the event's days"),
    ({"url": "http://example.ch"}, "https://"),
    ({"verified": "yes"}, "verified"),
    ({"verified": False}, "todo"),
    ({"colour": "red"}, "unknown key"),
    ({"hours": {day: None for day in events.WEEKDAYS}}, "never open"),
])
def test_validation_catches(changes, message):
    assert message in _problems(_valid_event(**changes))


def test_late_close_must_not_be_before_close():
    hours = {day: {"open": "18:00", "close": "02:00", "late_close": "23:00"} for day in events.WEEKDAYS}
    assert "late_close" in _problems(_valid_event(hours=hours))


def test_slugs_are_unique():
    assert "used by another event" in _problems(_valid_event(), _valid_event())


def test_the_app_refuses_a_broken_file(tmp_path):
    path = tmp_path / "events.json"
    path.write_text('{"events": [{"slug": "x"}')
    with pytest.raises(events.EventDataError):
        events.load(path)


# --- Hours and late departures back ---------------------------------------------

MONTREUX = events.BY_SLUG["montreux-noel-2026"]
BO_NOEL = events.BY_SLUG["bo-noel-lausanne-2026"]
BASEL = events.BY_SLUG["basel-christmas-market-2026"]


def test_hours_vary_by_weekday():
    tuesday = events.hours_on(MONTREUX, date(2026, 11, 24))
    saturday = events.hours_on(MONTREUX, date(2026, 11, 28))
    assert (tuesday["open"].hour, tuesday["close"].hour) == (11, 21)
    assert (saturday["open"].hour, saturday["close"].hour) == (10, 22)


def test_exceptions_override_the_weekday():
    assert events.hours_on(BO_NOEL, date(2026, 12, 25)) is None
    christmas_eve = events.hours_on(MONTREUX, date(2026, 12, 24))
    assert christmas_eve["close"] == datetime(2026, 12, 24, 17, 0)
    assert christmas_eve["late_close"] is None
    assert events.hours_on(BASEL, date(2026, 12, 23))["close"] == datetime(2026, 12, 23, 20, 0)


def test_not_open_outside_its_days():
    assert events.hours_on(BASEL, date(2026, 11, 25)) is None
    assert events.hours_on(BASEL, date(2026, 12, 24)) is None


def test_return_times_from_closing_until_90_minutes_after():
    basel = events.return_times(events.hours_on(BASEL, date(2026, 11, 30)))
    assert [t.strftime("%H:%M") for t in basel] == ["20:30", "21:00", "21:30", "22:00"]


def test_return_times_include_when_the_bars_close():
    # Shops close at 20:00, food and drinks at 23:00.
    friday = events.return_times(events.hours_on(BO_NOEL, date(2026, 11, 27)))
    assert [t.strftime("%H:%M") for t in friday] == ["20:00", "20:30", "21:00", "21:30", "23:00"]


def test_closing_after_midnight_is_the_next_day():
    new_year = events.hours_on(BO_NOEL, date(2026, 12, 31))
    assert new_year["late_close"] == datetime(2027, 1, 1, 2, 0)
    assert new_year["verified"] is False
    assert events.return_times(new_year)[-1] == datetime(2027, 1, 1, 2, 0)


def test_schedule_lists_the_open_days():
    days = events.schedule(BO_NOEL)
    assert days[0]["date"] == "2026-11-20"
    assert days[-1]["date"] == "2026-12-31"
    assert "2026-12-25" not in [d["date"] for d in days]
    assert len(days) == 41


# --- API ----------------------------------------------------------------------------

def test_list_has_current_and_upcoming_only(monkeypatch):
    monkeypatch.setattr(events, "today", lambda: date(2026, 12, 24))
    listed = main.list_events(lang="de")
    assert [e["slug"] for e in listed] == ["bo-noel-lausanne-2026", "montreux-noel-2026"]
    assert all(e["status"] == "current" for e in listed)

    monkeypatch.setattr(events, "today", lambda: date(2026, 11, 18))
    listed = {e["slug"]: e for e in main.list_events(lang="fr")}
    assert listed["basel-christmas-market-2026"]["name"] == "Marché de Noël de Bâle"
    assert listed["basel-christmas-market-2026"]["status"] == "upcoming"


def test_one_event_with_its_schedule():
    event = main.get_event("basel-christmas-market-2026", lang="it")
    assert event["name"] == "Mercatino di Natale di Basilea"
    assert event["place"]["station"] == "Basel SBB"
    assert event["schedule"][0]["return_times"][0] == "2026-11-26T20:30"


def test_unknown_event_is_404():
    with pytest.raises(HTTPException) as e:
        main.get_event("no-such-event")
    assert e.value.status_code == 404
    assert e.value.detail["code"] == "event_not_found"


# --- Shareable link ------------------------------------------------------------------

def test_share_link_redirects_to_the_event_page():
    response = main.event_link("montreux-noel-2026", lang=None, accept_language="fr-CH,fr;q=0.9,en;q=0.8")
    page = response.body.decode()
    assert response.status_code == 200
    assert 'url=/#/event/montreux-noel-2026"' in page
    assert '<html lang="fr">' in page
    assert "Covoiturage pour Montreux Noël" in page
    assert "20.11.–24.12.2026" in page


def test_share_link_passes_the_language_on():
    page = main.event_link("basel-christmas-market-2026", lang="de", accept_language=None).body.decode()
    assert "/?lang=de#/event/basel-christmas-market-2026" in page
    assert "Basler Weihnachtsmarkt — Ride and Rail" in page


def test_share_link_for_an_unknown_event_is_escaped():
    response = main.event_link('x"><script>alert(1)</script>', lang="xx", accept_language=None)
    page = response.body.decode()
    assert response.status_code == 404
    assert "<script>alert" not in page
    assert "?lang=xx" not in page


def test_share_link_route_comes_before_the_static_files():
    paths = [getattr(r, "path", None) for r in main.app.routes]
    assert paths.index("/e/{slug}") < paths.index("")


# --- Demo drives ----------------------------------------------------------------

def test_demo_drives_to_and_from_each_event_every_open_day():
    for event in events.EVENTS:
        place = event["place"]["name"]
        for day in events.schedule(event):
            on_day = [s for s in demo_drives.EVENT_DRIVES if s["departure"].date().isoformat() == day["date"]]
            there = [s for s in on_day if s["destination"]["name"] == place]
            back = [s for s in on_day if s["origin"]["name"] == place]
            assert len(there) >= demo_drives.EVENT_ORIGINS, (event["slug"], day["date"])
            assert len(back) == len(there)
            # Back between closing time and 1.5 hours after.
            closes = datetime.fromisoformat(day["close"])
            assert all(closes <= s["departure"] <= closes + events.RETURN_WINDOW for s in back)
            # There while it's open.
            opens = datetime.fromisoformat(day["open"])
            for s in there:
                km = matching.haversine_km(s["origin"]["lat"], s["origin"]["lon"], *[event["place"][k] for k in ("lat", "lon")])
                assert s["departure"] < closes
                assert s["departure"] + timedelta(minutes=km * 1.3 + 5) >= opens


def test_event_drives_are_in_the_rolling_week(isolated_db, force_haversine):
    now = datetime(2026, 11, 27, 9, 0)
    demo_drives.refresh(now)
    drives = main.db.list_drives(upcoming_only=False, with_geometry=False)
    place = MONTREUX["place"]["name"]
    to_montreux = [d for d in drives if d["destination_name"] == place]
    assert to_montreux and all(d["is_demo"] for d in to_montreux)
    assert min(d["departure_time"] for d in to_montreux) > "2026-11-27T09:00"
    assert max(d["departure_time"] for d in to_montreux) < "2026-12-04T00:00"
    # Idempotent, like the weekly drives.
    assert demo_drives.refresh(now) == (0, 0)


def test_a_passenger_going_back_from_the_market_finds_a_ride(isolated_db, force_haversine, monkeypatch):
    monkeypatch.setattr(main.db, "now_iso", lambda: "2026-11-27T09:00:00")
    demo_drives.refresh(datetime(2026, 11, 27, 9, 0))
    day = next(d for d in events.schedule(BASEL) if d["date"] == "2026-11-28")
    venue = (BASEL["place"]["lat"], BASEL["place"]["lon"])
    liestal = demo_drives.PLACES["Liestal"]
    found = main.search.upcoming_matches(venue, liestal, day["return_times"][1], time_tolerance_min=60)
    assert found
