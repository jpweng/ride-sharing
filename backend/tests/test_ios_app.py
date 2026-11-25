"""
The site as the iOS app shows it (ios_app.py, frontend/ios_app.js): a
request that names the app in its User-Agent is answered without the
demo, a browser's as before; and the pages mark everything they say about
the demo, so that the app can hide it. Isolated database, offline.
"""
import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import database as db
import demo_drives
import main

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PAGES = ("index.html", "about.html", "privacy.html", "conduct.html", "contact.html", "offline.html")
LANGS = ("en", "de", "fr", "it")

SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
# What the app's web view sends (mobile/ios/App/WebViewController.swift).
APP = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 RideAndRailApp/1.0"

VEVEY = {"name": "Vevey", "lat": 46.4628, "lon": 6.8419}
FRIBOURG = {"name": "Fribourg", "lat": 46.8065, "lon": 7.1619}
TOMORROW = date.today() + timedelta(days=1)


@pytest.fixture
def browser(isolated_db):
    return TestClient(main.app, headers={"User-Agent": SAFARI})


@pytest.fixture
def app(isolated_db):
    return TestClient(main.app, headers={"User-Agent": APP})


def _drive(driver, is_demo):
    return db.create_drive(
        driver_name=driver, origin_name="Vevey", origin_lat=VEVEY["lat"], origin_lon=VEVEY["lon"],
        destination_name="Fribourg", destination_lat=FRIBOURG["lat"], destination_lon=FRIBOURG["lon"],
        departure_time=f"{TOMORROW}T18:00", seats_available=3, route_geometry=None,
        route_distance_m=None, route_duration_s=None, route_source="haversine", is_demo=is_demo,
    )


def _search(client):
    r = client.post("/api/match", json={"origin": VEVEY, "destination": FRIBOURG, "requested_time": f"{TOMORROW}T18:00"})
    assert r.status_code == 200, r.text
    return [m["id"] for m in r.json()["driver_matches"]]


# --- What the server answers ---------------------------------------------------------

def test_a_search_in_the_app_finds_no_fictional_drive(browser, app, force_haversine, fake_transit):
    demo_id = _drive("Demo Driver", is_demo=True)
    real_id = _drive("Real Driver", is_demo=False)

    assert _search(browser) == [real_id, demo_id]
    assert _search(app) == [real_id]
    # Each request by itself: a browser's, right after the app's, is answered as before.
    assert _search(browser) == [real_id, demo_id]


def test_the_list_of_drives_and_a_drive_by_its_number(browser, app, force_haversine):
    demo_id = _drive("Demo Driver", is_demo=True)
    real_id = _drive("Real Driver", is_demo=False)

    assert {d["id"] for d in browser.get("/api/drives").json()} == {demo_id, real_id}
    assert {d["id"] for d in app.get("/api/drives").json()} == {real_id}
    assert browser.get(f"/api/drives/{demo_id}").status_code == 200
    assert app.get(f"/api/drives/{demo_id}").status_code == 404
    assert app.get(f"/api/drives/{real_id}").status_code == 200


def test_no_view_as_a_demo_driver_in_the_app(browser, app):
    names = browser.get("/api/auth/demo_drivers").json()
    assert names
    assert app.get("/api/auth/demo_drivers").json() == []

    r = app.post("/api/auth/demo", json={"driver_name": names[0]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "demo_driver_not_found"
    assert app.get("/api/auth/me").json()["user"] is None
    assert browser.post("/api/auth/demo", json={"driver_name": names[0]}).status_code == 200


def test_nothing_is_deleted_and_the_switch_still_rules(app, force_haversine, monkeypatch):
    demo_id = _drive("Demo Driver", is_demo=True)
    app.get("/api/drives")
    assert db.get_drive(demo_id) is not None
    # Outside a request (the hourly upkeep), demo drives are what the switch says.
    assert demo_drives.shown() is True
    monkeypatch.setenv("DEMO_DRIVES", "off")
    assert demo_drives.shown() is False


def test_only_the_apps_own_name_counts(isolated_db, force_haversine):
    demo_id = _drive("Demo Driver", is_demo=True)
    for agent in ("", "RideAndRail", "rideandrailapp/1.0", SAFARI + " SomeOtherApp/2.0"):
        client = TestClient(main.app, headers={"User-Agent": agent})
        assert [d["id"] for d in client.get("/api/drives").json()] == [demo_id], agent


# --- What the pages hide ---------------------------------------------------------------

DEMO_WORD = re.compile(r"d[eé]mo|prototyp|fictional|fictif|fittiz|erfunden|made-up", re.IGNORECASE)


class _Shown(HTMLParser):
    """A page's text as the app shows it: without what is marked demo-only,
    and without scripts and styles."""

    VOID = {"meta", "link", "br", "input", "img", "hr"}

    def __init__(self):
        super().__init__()
        self.text = []
        self.hidden = []  # the open tags inside something hidden

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        classes = (dict(attrs).get("class") or "").split()
        if self.hidden or "demo-only" in classes or tag in ("script", "style"):
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and tag not in self.VOID:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def _shown(html: str) -> str:
    parser = _Shown()
    parser.feed(html)
    return " ".join(" ".join(parser.text).split())


@pytest.mark.parametrize("page", PAGES)
def test_a_page_says_nothing_about_the_demo_in_the_app(page):
    source = (FRONTEND / page).read_text()
    found = DEMO_WORD.findall(_shown(source))
    assert found == [], f"{page} still says {sorted(set(found))} outside class=\"demo-only\""
    # The same pages do say it to everybody else.
    if page not in ("offline.html",):
        assert DEMO_WORD.search(source)


@pytest.mark.parametrize("page", PAGES)
def test_every_page_knows_the_app_before_it_draws(page):
    head = (FRONTEND / page).read_text().split("</head>")[0]
    assert '<script src="ios_app.js"></script>' in head
    assert head.index("ios_app.js") < head.index('href="style.css"')


def test_the_rule_that_hides():
    assert ".without-demo .demo-only { display: none !important; }" in (FRONTEND / "style.css").read_text()
    script = (FRONTEND / "ios_app.js").read_text()
    assert "RideAndRailApp" in script and '"without-demo"' in script


@pytest.mark.parametrize("lang", LANGS)
def test_the_banners_in_every_language(lang):
    banner = json.loads((FRONTEND / "i18n" / lang / "banner.json").read_text())
    for key in ("banner.app", "banner.privacy"):
        assert DEMO_WORD.search(banner[key])
        assert DEMO_WORD.findall(_shown(banner[key])) == [], (lang, key)
        assert len(_shown(banner[key])) > 5  # the links stay


@pytest.mark.parametrize("lang", LANGS)
def test_the_text_for_a_request_nobody_was_told_about(lang):
    ride = json.loads((FRONTEND / "i18n" / lang / "ride.json").read_text())
    assert DEMO_WORD.search(ride["ride.askSentNone"])
    assert not DEMO_WORD.search(ride["ride.askSentNoneWithoutDemo"])
    # The same sentence otherwise: without its words in brackets.
    assert ride["ride.askSentNoneWithoutDemo"] == re.sub(r" \([^)]*\)", "", ride["ride.askSentNone"])
    assert 'WITHOUT_DEMO ? "ride.askSentNoneWithoutDemo" : "ride.askSentNone"' in (FRONTEND / "app.js").read_text()


@pytest.mark.parametrize("lang", LANGS)
def test_the_about_page_and_the_privacy_notice_in_every_language(lang):
    for page, marks in (("about.html", 3), ("privacy.html", 1)):
        source = (FRONTEND / page).read_text()
        article = re.search(rf'<article[^>]*lang="{lang}"[^>]*>(.*?)</article>', source, re.S).group(1)
        assert article.count("demo-only") == marks, (page, lang)
        assert DEMO_WORD.findall(_shown(article)) == [], (page, lang)
