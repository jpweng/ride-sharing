"""
Picking a place on the map (app.js): a click proposes what is there, a named
place or the address, in a card; "Use this place" takes it. Static checks of
the script and the texts; the behaviour itself runs in the browser.
"""
import json
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
APP = (FRONTEND / "app.js").read_text()


def test_a_click_proposes_a_place_instead_of_setting_the_point():
    handler = APP[APP.index('map.on("click", async (e) => {'):APP.index("function proposePlace(")]
    assert "placesAt(lat, lng)" in handler and "proposePlace(role, popup, marker, places)" in handler
    # The point is set by "Use this place" only.
    assert "setPointCoords" not in handler
    use = APP[APP.index("function usePlace("):APP.index("function setPointCoords(")]
    assert "setPointCoords(role, place.lat, place.lon, place.label, place.kind)" in use


def test_places_come_from_the_reverse_search_already_declared_in_the_privacy_page():
    assert 'const PHOTON_REVERSE_URL = "https://photon.komoot.io/reverse";' in APP
    privacy = (FRONTEND / "privacy.html").read_text()
    # Each language says that the points clicked go to Photon.
    assert len(re.findall(r"Photon (also receives|erhält zudem|reçoit aussi|riceve inoltre)", privacy)) == 4


def test_photon_is_only_asked_in_a_language_it_answers_in():
    # It refuses the others (Italian) with an error: no place would be found.
    assert 'const photonLang = () => (["en", "de", "fr"].includes(I18N.lang()) ? I18N.lang() : "default");' in APP
    assert "lang=${I18N.lang()}&bbox" not in APP
    assert APP.count("lang=${photonLang()}") == 2


def test_the_cards_buttons_act_once_the_click_is_over():
    # A button removed from the card during its own click would count as a
    # click on the map (Leaflet looks for the card around the button).
    card = APP[APP.index("function proposePlace("):APP.index("function usePlace(")]
    assert "setTimeout(() => usePlace(role, place), 0)" in card
    assert re.search(r'button\.addEventListener\("click", \(\) => setTimeout\(', card)


def test_texts_in_every_language():
    keys = {"map.useThisPlace", "map.nearby", "map.station"}
    for lang in ("en", "de", "fr", "it"):
        texts = json.loads((FRONTEND / "i18n" / lang / "map.json").read_text())
        assert keys <= texts.keys(), lang
        assert "{name}" in texts["map.station"], lang
