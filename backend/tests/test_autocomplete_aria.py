"""
The address autocomplete (app.js) is an editable combobox linked to a listbox:
the option Enter would choose is exposed through aria-activedescendant. Static
checks of the script; the behaviour itself runs in the browser.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
APP = (FRONTEND / "app.js").read_text()
INDEX = (FRONTEND / "index.html").read_text()
SETUP = APP[APP.index("function setupAutocomplete("):APP.index("Object.keys(PICK_COLORS).forEach(setupAutocomplete)")]


def test_the_field_is_a_combobox_controlling_its_listbox():
    for attr in ('"role", "combobox"', '"aria-autocomplete", "list"', '"aria-controls", list.id',
                 '"aria-expanded", "false"', '"role", "listbox"'):
        assert attr in SETUP, attr


def test_options_have_stable_ids_and_the_active_one_is_the_one_enter_selects():
    assert 'id="${list.id}-opt-${i}" role="option"' in SETUP
    assert 'aria-selected="${i === activeIndex}"' in SETUP
    # The same activeIndex drives the highlight, the descendant and Enter.
    assert "`${list.id}-opt-${activeIndex}`" in SETUP
    assert 'e.key === "Enter" && activeIndex >= 0' in SETUP
    assert "selectItem(items[activeIndex])" in SETUP


def test_the_address_is_part_of_the_option_and_separated_from_the_name():
    assert '` <span class="suggest-detail">${esc(it.detail)}</span>`' in SETUP


def test_expanded_and_descendant_follow_the_list_and_clear_on_dismissal():
    sync = SETUP[SETUP.index("function syncAria()"):SETUP.index("function closeList()")]
    assert 'setAttribute("aria-expanded", String(open))' in sync
    assert 'removeAttribute("aria-activedescendant")' in sync
    close = SETUP[SETUP.index("function closeList()"):SETUP.index("function renderList()")]
    assert "activeIndex = -1;" in close and "syncAria();" in close
    # Arrow keys re-render, which updates the descendant.
    assert len(re.findall(r"moveActive\(-?1\);\s+renderList\(\)", SETUP)) == 2


def test_the_passenger_fields_have_a_list_with_an_id():
    for role in ("ride-origin", "ride-destination"):
        assert f'<ul class="autocomplete-list" id="suggest-{role}"></ul>' in INDEX
