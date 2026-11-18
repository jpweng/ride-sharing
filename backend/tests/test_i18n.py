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
PAGES = ["index.html", "privacy.html", "conduct.html"]
# Pages translated as whole texts, one <article lang="xx"> per language.
DOC_PAGES = ["privacy.html", "conduct.html"]


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


# Visitors are addressed formally everywhere: "Sie" (de), "vous" (fr), "Lei" (it).
INFORMAL = {
    "de": r"\b(du|dich|dir|dein\w*|bist|hast|kannst|musst|gib|wähle|verwende|klicke)\b",
    "fr": r"\b(tu|toi|ton|ta|tes)\b",
    # Only forms that can't also be formal ("usa", "clicca", "digita" are both).
    "it": r"\b(tu|ti|te|tuo|tua|tuoi|tue|sei|hai|puoi|vuoi|devi|scegli|inserisci|offri)\b",
}


@pytest.mark.parametrize("lang", INFORMAL)
def test_formal_tone(lang):
    articles = [
        (page, re.search(rf'<article[^>]*lang="{lang}".*?</article>', (FRONTEND / page).read_text(), re.S).group(0))
        for page in DOC_PAGES
    ]
    for key, text in [*DICTS[lang].items(), *articles]:
        assert not re.findall(INFORMAL[lang], text, re.I), key


@pytest.mark.parametrize("page", DOC_PAGES)
def test_documents_in_every_language(page):
    source = (FRONTEND / page).read_text()
    assert set(re.findall(r'<article[^>]*lang="(\w+)"[^>]*data-i18n-block', source)) == set(LANGS)


class _KeyedElements(HTMLParser):
    """Collects i18n keys, and the built-in text of data-i18n elements."""

    ATTRS = ("data-i18n", "data-i18n-html", "data-i18n-placeholder", "data-i18n-aria-label", "data-i18n-title")

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
    source = "".join(path.read_text() for path in (ROOT / "backend").glob("*.py"))
    codes = set(re.findall(r'(?:api_error|AuthError)\(\d+, "(\w+)"', source))
    assert {"not_signed_in", "invalid_link"} <= codes
    assert {f"errors.{code}" for code in codes} - set(EN) == set()


def test_email_keys_exist():
    # mailer.py's texts: t("<key>") is email.signIn.<key>.
    source = (ROOT / "backend" / "mailer.py").read_text()
    keys = set(re.findall(r'\bt\("(\w+)"', source))
    assert keys
    assert {f"email.signIn.{key}" for key in keys} - set(EN) == set()
