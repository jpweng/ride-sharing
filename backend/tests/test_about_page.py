"""
The About page (frontend/about.html, docs/about-page.md): what Ride and Rail
offers, one text per language. It has to stay true to the site and the apps,
so these checks tie it to the code where they can: the four texts keep the
same structure (a paragraph added in one language is added in all), its
links lead somewhere, the app links to it, and the facts it takes from the
code (retention, age, languages, the price ceiling) are still the code's.

What a test can't read (whether a sentence still describes a feature) is the
rule in CLAUDE.md: a change to what users can do changes this page too.
"""
import json
import re
from pathlib import Path

import pytest

import maintenance
import pricing
import translations

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
LANGS = ["en", "de", "fr", "it"]
PAGE = (FRONTEND / "about.html").read_text()
ARTICLES = {
    lang: re.search(rf'<article[^>]*lang="{lang}".*?</article>', PAGE, re.S).group(0)
    for lang in LANGS
}
EN = ARTICLES["en"]


def _shape(article):
    """The article's elements in order: the tags, and where its links lead."""
    tags = re.findall(r"<(h2|h3|p|ul|li|a|span)\b", article)
    return tags, re.findall(r'href="([^"]*)"', article)


@pytest.mark.parametrize("lang", LANGS[1:])
def test_every_language_says_the_same_things(lang):
    assert _shape(ARTICLES[lang]) == _shape(EN)


def test_its_links_lead_somewhere():
    links = set(_shape(EN)[1])
    assert {"privacy.html", "conduct.html", "./"} <= links
    for href in links - {"./"}:
        assert (FRONTEND / href).is_file(), href


def test_the_app_links_to_it():
    assert '<a href="about.html">About</a>' in (FRONTEND / "index.html").read_text()
    for lang in LANGS:
        banner = json.loads((FRONTEND / "i18n" / lang / "banner.json").read_text())
        assert 'href="about.html"' in banner["banner.app"], lang


def test_the_sections_it_has():
    # One per thing the service offers; a new one is added in every language
    # (test_every_language_says_the_same_things).
    assert re.findall(r"<h3>(.*?)</h3>", EN) == [
        "If you need a ride",
        "If you drive",
        "Sharing costs, not earning money",
        "Knowing who you travel with",
        "On your phone and your computer",
        "A demo for now",
        "Your data",
    ]


@pytest.mark.parametrize("lang", LANGS)
def test_data_is_kept_as_long_as_it_says(lang):
    days = maintenance.RETENTION.days
    assert days == 30
    assert re.search(rf"\b{days}\b", ARTICLES[lang]), lang


@pytest.mark.parametrize("lang", LANGS)
def test_the_age_it_says(lang):
    # The age asked for at first sign-in (profile.adult, the code of conduct).
    profile = json.loads((FRONTEND / "i18n" / lang / "profile.json").read_text())
    assert "18" in profile["profile.adult"]
    assert re.search(r"\b18\b", ARTICLES[lang]), lang


def test_the_languages_it_names():
    assert set(translations.LANGUAGES) == {"de", "fr", "it", "en"}
    assert "in German, French, Italian and English" in " ".join(EN.split())


def test_a_ride_never_costs_more_than_the_train():
    # "never more than the train's 2nd class full fare for the same distance"
    assert "never more than the train's 2nd class full fare" in " ".join(EN.split())
    for km, train_chf in ((5, 3.0), (40, 9.0), (150, 30.0)):
        for passengers in (1, 3):
            assert pricing.passenger_price_chf(1000.0, km, passengers, train_full_chf=train_chf) <= train_chf
    # "When more passengers share a drive, each pays less."
    assert pricing.passenger_price_chf(1000.0, 40, 3) < pricing.passenger_price_chf(1000.0, 40, 1)
