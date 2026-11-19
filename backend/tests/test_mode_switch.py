"""
The passenger/driver mode switch in the navigation bar: one button that
names the current mode and switches to the other one when clicked (app.js:
renderModeSwitch), not a menu.
"""
import json
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def test_mode_switch_is_a_single_button():
    html = (FRONTEND / "index.html").read_text()
    nav = re.search(r'<nav class="tabs">.*?</nav>', html, re.S).group(0)
    assert re.search(r'<button type="button"[^>]*\bid="mode-switch"', nav)
    assert "<details" not in nav


def test_mode_switch_texts_in_every_language():
    # The button's label (the current mode) and its tooltip (the switch).
    keys = {"nav.passengerMode", "nav.driverMode", "nav.toDriver", "nav.toPassenger"}
    for lang in ("en", "de", "fr", "it"):
        texts = json.loads((FRONTEND / "i18n" / lang / "nav.json").read_text())
        assert keys <= texts.keys(), lang
        assert all(texts[key].strip() for key in keys), lang
