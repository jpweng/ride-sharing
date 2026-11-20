"""
The passenger page (docs/passenger-page.md): one card with a From and a To
field, no search button (the search starts by itself, passenger.js), and
the map out of the way in passenger mode until it's asked for
(styles/passenger.css, app.js: openMap). Static checks of the files; the
behaviour itself runs in the browser.
"""
import json
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
HTML = (FRONTEND / "index.html").read_text()
RIDE = re.search(r'<section id="page-ride".*?</section>', HTML, re.S).group(0)


def test_the_form_is_two_fields_and_a_swap_button():
    assert re.findall(r'<input type="text" id="(name-ride-[a-z]+)"', RIDE) == ["name-ride-origin", "name-ride-destination"]
    assert 'id="ride-swap"' in RIDE
    # Gone: the "Pick on map" buttons, the coordinates, the search button,
    # the explanation and the list of recent searches.
    for gone in ("data-role=", "coord-readout", "ride-submit", "ride.searchHint", "ride-recent"):
        assert gone not in RIDE, gone


def test_the_trip_card_is_on_the_page_and_one_bar_on_a_wide_screen():
    css = (FRONTEND / "styles" / "passenger.css").read_text()
    # No band of colour behind the card.
    assert "linear-gradient" not in css and "main:has(#page-ride" not in css
    # From, To and when are in one element, a row on a wide screen, where
    # the time shows from the start.
    bar = re.search(r'<div class="trip-bar">(.*)<div class="status-msg" id="ride-status">', RIDE, re.S).group(1)
    assert 'class="trip-card"' in bar and 'id="ride-when"' in bar
    wide = css[css.index("@media (min-width: 980px)"):css.index("@media (min-width: 1100px)")]
    assert re.search(r"\.trip-bar \.ride-when\[hidden\] \{\s*display: flex", wide)
    assert re.search(r"\.trip-bar \.trip-swap svg \{ transform: rotate\(90deg\)", wide)


def test_a_match_starts_with_who_drives():
    app = (FRONTEND / "app.js").read_text()
    card = app[app.index("card.innerHTML = `"):app.index("container.appendChild(card);")]
    head = card[card.index('<div class="match-head">'):card.index('${meetingPointSentence("pickup"')]
    # The circle (initial or photo), the name, then the drive.
    assert head.index("${driverAvatar(m)}") < head.index('class="match-name"') < head.index("m.origin_name")
    assert 't("match.driver")' in head  # signed out: no name
    # The rating goes next to the name, the photo into the circle.
    assert 'card.querySelector(".match-name, .route")' in (FRONTEND / "ratings.js").read_text()
    profiles = (FRONTEND / "profiles.js").read_text()
    assert "item.driver_photo" in profiles and 'card.querySelector(".avatar")' in profiles


def test_without_a_ride_asking_the_drivers_leads():
    app = (FRONTEND / "app.js").read_text()
    # The button is the filled one when nothing was found, the outlined one under rides
    # (a ride for part of the way, combined.js, is one too).
    assert 'getElementById("ride-ask").classList.toggle("secondary", matches.length + combined > 0)' in app
    assert 'classList.toggle("no-matches", !matches.length && !combined)' in app
    assert '<img src="icons/no-rides.svg" alt=""' in app
    assert (FRONTEND / "icons" / "no-rides.svg").is_file()


def test_public_transport_is_one_line_of_plain_rows():
    app = (FRONTEND / "app.js").read_text()
    option = app[app.index("function renderTransitOption(transit) {"):app.index("function renderFare(fare) {")]
    assert "<details" in option and 't("transit.summary"' in option
    # No title of its own above it.
    assert "ride.transitTitle" not in RIDE
    # No operator's logo, and the tickets are a link in the text's colours.
    assert "logo_url" not in app and "pt-logo" not in app
    css = (FRONTEND / "styles" / "transit.css").read_text()
    assert "pt-logo" not in css
    book = re.search(r"\.pt-book \{(.*?)\}", css, re.S).group(1)
    assert "background" not in book
    for lang in ("en", "de", "fr", "it"):
        texts = json.loads((FRONTEND / "i18n" / lang / "transit.json").read_text())
        assert "<" not in texts["transit.tickets"], lang


def test_place_fields_dont_look_like_name_or_address_fields_to_a_browser():
    # A browser's own suggestions (a saved name or address) would lie over
    # ours. Chromium ignores autocomplete="off" for those and guesses from the
    # field's name, or its id when it has none: "name-ride-origin" is a name.
    fields = re.findall(r'<input type="text" id="name-[a-z-]+"[^>]*>', HTML)
    assert len(fields) == 5
    for field in fields:
        name = re.search(r' name="([^"]+)"', field)
        assert name, field
        assert not re.search(r"name|address|street|city|zip|post|phone|mail", name.group(1)), field
        assert 'autocomplete="off"' in field, field


def test_the_time_and_the_lists_show_only_when_there_is_something():
    for hidden in ("ride-when", "ride-results", "ride-requests-title", "ride-requests"):
        assert re.search(rf'<div[^>]*\bid="{hidden}"[^>]*\bhidden\b', RIDE), hidden


def test_the_search_starts_by_itself():
    script = (FRONTEND / "passenger.js").read_text()
    assert 'window.addEventListener("point:set"' in script and "findRide()" in script
    # Loaded after app.js, whose map, points and search it uses.
    scripts = re.findall(r'<script src="([^"]+)"', HTML)
    assert scripts.index("passenger.js") > scripts.index("app.js")


def test_the_map_is_out_of_the_way_in_passenger_mode():
    css = (FRONTEND / "styles" / "passenger.css").read_text()
    hidden = re.search(r'body\[data-mode="passenger"\] #map \{(.*?)\}', css, re.S).group(1)
    # Hidden but with its size: routes are still drawn and framed on it.
    assert "visibility: hidden" in hidden and "display: none" not in hidden
    assert re.search(r'body\[data-mode="passenger"\]\.map-open #map \{[^}]*visibility: visible', css)
    for control in ("map-close", "map-backdrop", "map-hint"):
        assert f'id="{control}"' in HTML, control
    app = (FRONTEND / "app.js").read_text()
    # Whatever shows something on the map opens it in passenger mode.
    assert re.search(r"function revealMap\(\) \{\s*if \(passengerMode\(\)\) return openMap\(\);", app)


def test_demo_rides_open_their_details_but_are_not_shown_on_the_map():
    app = (FRONTEND / "app.js").read_text()
    card = app[app.index('more.className = "link-btn match-more";'):app.index("if (m.route_geometry && m.route_geometry.length)")]
    # "Details" is on every card, and nothing stops a demo card's click before it opens them.
    assert "card.appendChild(more);" in card and "if (!m.is_demo) card.appendChild(more)" not in card
    assert "if (m.is_demo) return choose();" not in card
    # "Show on map" on a demo ride: the notice that it is fictional (choose).
    assert 'if (e.target.closest(".match-on-map")) return !m.is_demo && selected === entry ? revealMap() : choose();' in card


def test_texts_in_every_language():
    keys = {
        "map": {"map.close", "map.pickHint"},
        "match": {"match.details", "match.showOnMap"},
        "ride": {"ride.swap", "ride.recentPlace"},
    }
    for lang in ("en", "de", "fr", "it"):
        for prefix, wanted in keys.items():
            texts = json.loads((FRONTEND / "i18n" / lang / f"{prefix}.json").read_text())
            assert wanted <= texts.keys(), (lang, prefix)
            assert all(texts[key].strip() for key in wanted), (lang, prefix)
