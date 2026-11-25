"""
A card says when its trip doesn't keep the time asked for
(frontend/asked_time.js, docs/search-limits.md): it leaves before a time to
leave at, or arrives after a time to arrive by. Static checks of the files,
as for the rest of the page; the behaviour itself runs in the browser.
"""
import json
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
SCRIPT = (FRONTEND / "asked_time.js").read_text()
APP = (FRONTEND / "app.js").read_text()
LANGS = ("en", "de", "fr", "it")


def test_the_page_loads_it_after_what_it_uses():
    html = (FRONTEND / "index.html").read_text()
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts.index("asked_time.js") > scripts.index("app.js")
    assert '<link rel="stylesheet" href="styles/asked_time.css" />' in html
    for name in ("CARD_EXTRAS", "lastResults", "minutesBetween", "fmtMinutes", "fmtClock", "MINUTE_STEP"):
        assert name in SCRIPT and re.search(rf"^(const|let|function) {name}\b", APP, re.M), name


def test_a_search_keeps_the_time_it_asked_for():
    # With the results, so a card rendered again (another language, a part
    # that arrives later) is still compared with the time of its own search.
    assert "lastResults = { data, origin, destination, asked: { time: payload.requested_time, kind: payload.time_kind } };" in APP


def test_the_note_is_about_the_times_on_the_card():
    # The two times a card's first line shows: the car's for a drive for the
    # whole trip, the passenger's own for a ride with public transport.
    assert "tripLine(m.pickup.time, m.dropoff.time," in APP
    assert "tripLine(m.starts, m.arrives," in (FRONTEND / "combined.js").read_text()
    assert 'kind === "combined" ? [item.starts, item.arrives] : [item.pickup.time, item.dropoff.time]' in SCRIPT
    assert '["match", "combined"].includes(kind)' in SCRIPT
    # Right under that line, on the card itself and not in its details.
    assert 'card.querySelector(".trip-line")' in SCRIPT and 'line.insertAdjacentHTML("afterend"' in SCRIPT


def test_only_a_trip_that_breaks_the_time_asked_for_says_so():
    note = SCRIPT[SCRIPT.index("function askedTimeNote"):SCRIPT.index("CARD_EXTRAS.push")]
    # To arrive by a time: arriving after it. To leave at a time: leaving
    # before it. Leaving later or arriving earlier is what was asked.
    assert "arrive ? minutesBetween(asked.time, arrives) : minutesBetween(starts, asked.time)" in note
    assert 'arrive ? t("asked.arrivesLater", words) : t("asked.leavesEarlier", words)' in note
    # A few minutes are no difference, as in a card's details.
    assert "if (!(off >= MINUTE_STEP)) return \"\";" in note
    assert "Math.abs(minutes) < MINUTE_STEP" in APP
    # No search to compare with: no note.
    assert 'if (!asked || !asked.time) return "";' in note


def test_the_note_gives_how_much_and_the_time_asked_for():
    for lang in LANGS:
        texts = json.loads((FRONTEND / "i18n" / lang / "asked.json").read_text())
        assert set(texts) == {"asked.arrivesLater", "asked.leavesEarlier"}, lang
        for text in texts.values():
            # "... 15 min ... (16:10)": the time they gave closes the sentence.
            assert "{duration}" in text and text.endswith("({time})"), (lang, text)
    english = json.loads((FRONTEND / "i18n" / "en" / "asked.json").read_text())
    assert english["asked.leavesEarlier"].startswith("Leaves {duration} before")
    assert english["asked.arrivesLater"].startswith("Arrives {duration} after")


def test_the_note_doesnt_rely_on_its_colour():
    # Words and a clock, the clock hidden from a screen reader.
    assert 'aria-hidden="true"' in SCRIPT and "${esc(note)}" in SCRIPT
    css = (FRONTEND / "styles" / "asked_time.css").read_text()
    assert ".asked-time-note {" in css
