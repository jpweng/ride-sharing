"""
The store listings (mobile/listing/<lang>.md, written by mobile/make_listing.py
from the About page): the committed files are what the script writes today,
so a change to the About page that isn't carried to the listings fails here,
and every text fits the store's field.
"""
import importlib.util
from pathlib import Path

import pytest

MOBILE = Path(__file__).resolve().parents[2] / "mobile"

spec = importlib.util.spec_from_file_location("make_listing", MOBILE / "make_listing.py")
make_listing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_listing)

LISTINGS = make_listing.listings()


@pytest.mark.parametrize("language", make_listing.LANGUAGES)
def test_the_committed_listing_follows_the_about_page(language):
    committed = (MOBILE / "listing" / f"{language}.md").read_text()
    assert committed == LISTINGS[language], "run: uv run python mobile/make_listing.py"


@pytest.mark.parametrize("language", make_listing.LANGUAGES)
def test_every_text_fits_its_field(language):
    fields = make_listing.fields(LISTINGS[language])
    assert set(fields) == set(make_listing.LIMITS)
    for key, limit in make_listing.LIMITS.items():
        assert 0 < len(fields[key]) <= limit, f"{language} {key}: {len(fields[key])} characters, {limit} at most"


@pytest.mark.parametrize("language", make_listing.LANGUAGES)
def test_the_description_is_the_about_pages_own_text(language):
    description = make_listing.fields(LISTINGS[language])["description"]
    about = " ".join((MOBILE.parent / "frontend/about.html").read_text().split())
    paragraphs = [line.removeprefix("• ") for line in description.split("\n") if line and not line.isupper()]
    assert len(paragraphs) > 15
    # Sentences without markup inside are in the page word for word.
    found = [p for p in paragraphs if p in about]
    assert len(found) >= len(paragraphs) - 6, [p for p in paragraphs if p not in about]


def test_what_the_listing_leaves_out():
    for language in make_listing.LANGUAGES:
        description = make_listing.fields(LISTINGS[language])["description"]
        # Nothing about paying through the site, which is switched on or off, ...
        assert "Stripe" not in description
        # ... and nothing saying that the apps are still to come.
        assert "Android" not in description and "iPhone" not in description
    english = make_listing.fields(LISTINGS["en"])["description"]
    assert "settle between themselves" not in english and "service fee" not in english
    assert "payouts" not in english
    assert english.startswith("A demo version for now.")
    assert "\n\nIF YOU NEED A RIDE\n• Say where you start" in english
    assert english.rstrip().endswith("The privacy notice has the details.")


def test_a_restructured_about_page_is_noticed():
    blocks = [("h2", "Title"), ("p", "Opening"), ("h3", "One"), ("p", "Text")]
    with pytest.raises(SystemExit, match="sections"):
        make_listing.description(blocks)
