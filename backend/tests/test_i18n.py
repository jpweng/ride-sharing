"""
Checks on the frontend translations (frontend/i18n/*.json): every language
has the same keys and placeholders, and every key the HTML, app.js and the
API's error codes use exists. A missing translation then fails CI instead of
showing a raw key on the site.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
LANGS = ["en", "de", "fr", "it"]
DICTS = {lang: json.loads((FRONTEND / "i18n" / f"{lang}.json").read_text()) for lang in LANGS}
EN = DICTS["en"]
PAGES = ["index.html", "privacy.html"]


def _placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


@pytest.mark.parametrize("lang", LANGS[1:])
def test_same_keys_as_english(lang):
    assert set(DICTS[lang]) == set(EN)


@pytest.mark.parametrize("lang", LANGS[1:])
def test_same_placeholders_as_english(lang):
    for key, text in EN.items():
        assert _placeholders(DICTS[lang][key]) == _placeholders(text), key


def test_swiss_german_spelling():
    assert not [k for k, v in DICTS["de"].items() if "ß" in v]


class _KeyedElements(HTMLParser):
    """Collects i18n keys, and the built-in text of data-i18n elements."""

    ATTRS = ("data-i18n", "data-i18n-html", "data-i18n-placeholder", "data-i18n-title")

    def __init__(self):
        super().__init__()
        self.keys = set()
        self.texts = []  # [key, English text] per data-i18n element
        self._open = []  # stack of (tag, index into texts or None)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.keys.update(attrs[a] for a in self.ATTRS if a in attrs)
        if tag not in ("input", "meta", "link", "br"):
            index = None
            if attrs.get("data-i18n"):
                index = len(self.texts)
                self.texts.append([attrs["data-i18n"], ""])
            self._open.append((tag, index))

    def handle_endtag(self, tag):
        while self._open and self._open.pop()[0] != tag:
            pass

    def handle_data(self, data):
        for _, index in self._open:
            if index is not None:
                self.texts[index][1] += data


def _parse(page):
    parser = _KeyedElements()
    parser.feed((FRONTEND / page).read_text())
    return parser


@pytest.mark.parametrize("page", PAGES)
def test_html_keys_exist(page):
    assert _parse(page).keys - set(EN) == set()


@pytest.mark.parametrize("page", PAGES)
def test_html_english_matches_dictionary(page):
    # The English built into the HTML shows if JavaScript fails; keep it in
    # sync with en.json.
    for key, text in _parse(page).texts:
        assert " ".join(text.split()) == EN[key], key


def test_app_js_keys_exist():
    source = (FRONTEND / "app.js").read_text()
    for key in set(re.findall(r'\bt\("([\w.]+)"', source)):
        # Plurals (a `count` param) use key.one / key.other.
        assert key in EN or f"{key}.other" in EN, key


def test_api_error_codes_translated():
    source = (ROOT / "backend" / "main.py").read_text()
    codes = set(re.findall(r'api_error\(\d+, "(\w+)"', source))
    assert codes
    assert {f"errors.{code}" for code in codes} - set(EN) == set()
