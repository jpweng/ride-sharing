"""
The UI's translations, in frontend/i18n/<lang>/<prefix>.json: one file per
key prefix (signin.json holds every "signin.*" key), so a new feature adds
its own file instead of editing a shared one. Keys are the full flat keys,
sorted within each file (backend/tests/test_i18n.py checks it).

The browser loads one dictionary per language, i18n/<lang>.json, which the
backend serves as the merge of that language's files (routes/i18n.py). The
sign-in email's texts come from the same dictionaries (mailer.py).
"""
import json
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent.parent / "frontend" / "i18n"
LANGUAGES = ("de", "fr", "it", "en")
# Each language's folder. A folder is looked up here, never built from the
# name a caller gives: that name can come from a request's address.
FOLDERS = {language: I18N_DIR / language for language in LANGUAGES}


def files(language: str) -> list[Path]:
    """A language's dictionary files, in name order. KeyError for anything
    that isn't one of LANGUAGES."""
    return sorted(FOLDERS[language].glob("*.json"))


def dictionary(language: str) -> dict[str, str]:
    """All of a language's keys, merged from its files (in name order, each
    file's keys sorted)."""
    merged = {}
    for path in files(language):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged
