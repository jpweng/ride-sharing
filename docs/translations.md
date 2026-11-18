# Translations

The UI is in German, French, Italian and English, with no library and no build step:

- [frontend/i18n/](../frontend/i18n/) holds one folder per language (`en/` is the reference and the
  fallback for missing keys), with one file per key prefix: `de/signin.json` holds every `signin.*`
  key, sorted. The browser loads `i18n/<lang>.json`, which the backend serves as the merge of that
  language's files ([backend/translations.py](../backend/translations.py)).
  [frontend/i18n.js](../frontend/i18n.js) picks the language: `?lang=xx`
  in the URL (also saved), then the visitor's saved choice, then the browser's languages, then
  English. The DE · FR · IT · EN picker in the header switches it.
- In HTML, `data-i18n="key"` (or `data-i18n-html`, `data-i18n-placeholder`) marks translated text;
  the English stays in the HTML as a fallback. In JavaScript, `t("key", { name })`; with a `count`
  param, plurals come from `key.one` / `key.other`. Dates and numbers use Swiss formats (`de-CH`
  etc.).
- The privacy notice is translated as whole texts: one `<article lang="xx" data-i18n-block>` per
  language in [frontend/privacy.html](../frontend/privacy.html).
- API errors carry a code (`{"detail": {"code": "drive_not_found", ...}}`); the frontend shows
  `errors.<code>` from the dictionary.

Visitors are addressed formally in every language ("Sie", "vous", "Lei"); Italian buttons use the
infinitive ("Trovare un passaggio"). To add a string, add its key to the same file in all four
languages (a new feature can add its own `<prefix>.json` to each).
[backend/tests/test_i18n.py](../backend/tests/test_i18n.py) fails if a key or placeholder is missing in
any language, if the HTML or a script (app.js, pwa.js) uses an unknown key, if the English in the HTML drifts from
the English dictionary, if a file holds another prefix's keys or unsorted keys, or if an informal form ("du", "tu") slips in. The translations were written without native review; have native speakers check them,
the privacy notice above all, before showing the site widely.

## Endpoint

- `GET  /i18n/<lang>.json` — public; a language's dictionary, merged from
  `frontend/i18n/<lang>/*.json` (see above), `no-cache`
