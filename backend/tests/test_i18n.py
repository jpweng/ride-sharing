"""
Checks on the frontend translations (frontend/i18n/<lang>/<prefix>.json):
every language has the same keys and placeholders, every key the HTML, the
scripts and the API's error codes use exists, and each file holds only its
own prefix's keys, sorted. A missing translation then fails CI instead of
showing a raw key on the site.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import translations

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
LANGS = ["en", "de", "fr", "it"]
# Each language's keys, merged from its files as the browser gets them.
DICTS = {lang: translations.dictionary(lang) for lang in LANGS}
EN = DICTS["en"]
# Every page and script is checked, a new one included, except those listed
# here with the reason.
UNCHECKED_SCRIPTS = {
    "i18n.js",  # the translation code itself; its only t("…") is in a comment
    "sw.js",  # the service worker shows no text (its error code is checked below)
    "payment_texts.js",  # shows or hides texts written in the pages themselves
    "ios_app.js",  # hides texts written in the pages themselves
}
PAGES = sorted(path.name for path in FRONTEND.glob("*.html"))
# Pages translated as whole texts, one <article lang="xx" data-i18n-block> per language.
DOC_PAGES = [page for page in PAGES if "data-i18n-block" in (FRONTEND / page).read_text()]
SCRIPTS = sorted(path.name for path in FRONTEND.glob("*.js") if path.name not in UNCHECKED_SCRIPTS)


def test_pages_and_scripts_found():
    # The globs above find at least the pages and scripts there were when they replaced fixed lists.
    assert {"index.html", "privacy.html", "conduct.html", "offline.html"} <= set(PAGES)
    assert {"privacy.html", "conduct.html", "offline.html"} <= set(DOC_PAGES)
    assert {"app.js", "pwa.js"} <= set(SCRIPTS)
    assert UNCHECKED_SCRIPTS <= {path.name for path in FRONTEND.glob("*.js")}


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
    # sync with the English dictionary.
    for key, text in _parse(page).texts:
        assert " ".join(text.split()) == EN[key], key


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_keys_exist(script):
    source = (FRONTEND / script).read_text()
    keys = set(re.findall(r'\bt\("([\w.]+)"', source))  # t("…") and I18N.t("…")
    assert keys
    for key in keys:
        # Plurals (a `count` param) use key.one / key.other.
        assert key in EN or f"{key}.other" in EN, key


def test_api_error_codes_translated():
    backend = ROOT / "backend"
    source = "".join(path.read_text() for path in backend.rglob("*.py") if "tests" not in path.relative_to(backend).parts)
    codes = set(re.findall(r'(?:api_error|AuthError)\(\d+, "(\w+)"', source))
    # The service worker answers API calls with "offline" when there's no network.
    codes |= set(re.findall(r'code: "(\w+)"', (FRONTEND / "sw.js").read_text()))
    assert {"not_signed_in", "invalid_link", "offline"} <= codes
    assert {f"errors.{code}" for code in codes} - set(EN) == set()


def test_email_keys_exist():
    # mailer.py's texts: t("<key>") is email.signIn.<key>.
    source = (ROOT / "backend" / "mailer.py").read_text()
    keys = set(re.findall(r'\bt\("(\w+)"', source))
    assert keys
    assert {f"email.signIn.{key}" for key in keys} - set(EN) == set()


def _key_files():
    return [(lang, path) for lang in LANGS for path in translations.files(lang)]


def test_every_language_has_files():
    assert set(translations.LANGUAGES) == set(LANGS)
    for lang in LANGS:
        assert translations.files(lang), lang
    # No dictionaries left at the old place: i18n/<lang>.json is served
    # by the backend, merged from i18n/<lang>/.
    assert not list((FRONTEND / "i18n").glob("*.json"))


@pytest.mark.parametrize("lang, path", _key_files(), ids=lambda v: getattr(v, "name", v))
def test_file_holds_its_prefix_sorted(lang, path):
    # signin.json holds the "signin.*" keys, in alphabetical order, so a
    # new key has one obvious place and parallel branches rarely conflict.
    keys = list(json.loads(path.read_text(encoding="utf-8")))
    assert keys
    assert keys == sorted(keys), path
    assert {key.split(".", 1)[0] for key in keys} == {path.stem}, path


def test_same_files_in_every_language():
    names = {lang: [path.name for path in translations.files(lang)] for lang in LANGS}
    for lang in LANGS[1:]:
        assert names[lang] == names["en"], lang


@pytest.mark.parametrize("lang", LANGS)
def test_served_dictionary_is_the_merge(lang):
    client = TestClient(main.app)
    resp = client.get(f"/i18n/{lang}.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.json() == DICTS[lang]
    again = client.get(f"/i18n/{lang}.json", headers={"If-None-Match": resp.headers["etag"]})
    assert again.status_code == 304


def test_unknown_language_not_found():
    assert TestClient(main.app).get("/i18n/xx.json").status_code == 404


def test_a_languages_folder_is_looked_up_never_built_from_the_name():
    assert set(translations.FOLDERS) == set(translations.LANGUAGES)
    for lang in LANGS:
        assert translations.FOLDERS[lang] == translations.I18N_DIR / lang
        assert translations.files(lang) == sorted((translations.I18N_DIR / lang).glob("*.json"))
    # Anything else has no folder, whatever it would point at as a path.
    for name in ("xx", "", "..", "en/..", "en/../de", "/etc", "EN"):
        with pytest.raises(KeyError):
            translations.files(name)
        with pytest.raises(KeyError):
            translations.dictionary(name)
