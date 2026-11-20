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


def test_the_time_and_the_lists_show_only_when_there_is_something():
    for hidden in ("ride-when", "ride-results", "ride-requests-title", "ride-requests"):
        assert re.search(rf'<div[^>]*\bid="{hidden}"[^>]*\bhidden\b', RIDE), hidden


def test_the_search_starts_by_itself():
    script = (FRONTEND / "passenger.js").read_text()
    assert '"point:set", "point:named"' in script and "findRide()" in script
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
