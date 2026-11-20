"""
The last trip searched is remembered in the browser for a day (passenger.js):
the search page comes back with it after a reload and in the tab the emailed
sign-in link opens; signing out forgets it; the privacy page says so. Static
checks of the script and the page; the behaviour itself runs in the browser.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
SCRIPT = (FRONTEND / "passenger.js").read_text()


def test_kept_on_the_device_for_a_day():
    assert 'const TRIP_STORAGE_KEY = "rideandrail.trip";' in SCRIPT
    assert "const TRIP_KEPT_MS = 24 * 60 * 60 * 1000;" in SCRIPT
    remembered = SCRIPT[SCRIPT.index("function rememberedTrip()"):SCRIPT.index("function restoreTrip()")]
    # Older than that, or not a trip: forgotten, not restored.
    assert "Date.now() - trip.saved < TRIP_KEPT_MS" in remembered and "forgetTrip();" in remembered
    # The browser's storage only: nothing of it goes to the server.
    storage = SCRIPT[SCRIPT.index("const TRIP_STORAGE_KEY"):]
    assert "fetch" not in storage and "postJson" not in storage


def test_remembered_when_a_search_starts_and_restored_into_the_empty_page():
    assert re.search(r"rememberTrip\(\);\s*findRide\(\);", SCRIPT)
    restore = SCRIPT[SCRIPT.index("function restoreTrip()"):SCRIPT.index("function clearTrip()")]
    # Never over what the passenger has entered.
    assert "if (RIDE_ROLES.some((role) => points[role]) || inputs.some((input) => input.value.trim())) return;" in restore
    assert re.search(r"PAGES\.ride\.onShow = \(\.\.\.args\) => \{\s*showRidePage\(\.\.\.args\);\s*restoreTrip\(\);", SCRIPT)


def test_signing_out_forgets_it():
    assert 'window.addEventListener("user:changed", () => (currentUser ? searchSoon() : clearTrip()));' in SCRIPT
    clear = SCRIPT[SCRIPT.index("function clearTrip()"):SCRIPT.index("// Hooking into app.js's page")]
    assert "forgetTrip();" in clear and "clearPoint(role);" in clear


def test_the_privacy_page_says_so_in_every_language():
    privacy = (FRONTEND / "privacy.html").read_text()
    for words in ("for 24 hours or until you sign out", "während 24 Stunden oder bis Sie sich abmelden",
                  "pendant 24 heures ou jusqu'à", "per 24 ore o finché si disconnette"):
        assert words in " ".join(privacy.split()), words
