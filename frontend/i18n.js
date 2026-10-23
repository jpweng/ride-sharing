"use strict";

// ---------------------------------------------------------------------
// Translations: one dictionary per language in i18n/<lang>.json, English
// being the reference and the fallback for missing keys.
//
// The language is, in order: ?lang=xx in the URL (also saved), the
// visitor's saved choice, the browser's languages, then English.
//
// In HTML:
//   data-i18n="key"              sets the element's text
//   data-i18n-html="key"         sets its HTML (dictionary strings are trusted)
//   data-i18n-placeholder="key"  sets its placeholder
//   data-i18n-block + lang="xx"  shown only in that language; for whole
//                                texts such as the privacy notice
//   <html data-i18n-title="key"> sets the page title
// In JavaScript: I18N.t("key", { name: "…" }), and with a `count` param,
// plurals from the "key.one" / "key.other" entries.
// ---------------------------------------------------------------------
const I18N = (() => {
  const LANGS = ["de", "fr", "it", "en"];
  // Swiss conventions (24-hour clock etc.) whatever the language.
  const LOCALES = { de: "de-CH", fr: "fr-CH", it: "it-CH", en: "en-CH" };
  const STORAGE_KEY = "rideandrail.lang";

  let lang = detectLang();
  let dict = {};
  let fallback = {};

  function savedLang() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null; // storage unavailable (private window etc.)
    }
  }

  function saveLang(l) {
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      // fine: the choice just won't be remembered
    }
  }

  function detectLang() {
    const fromUrl = new URLSearchParams(location.search).get("lang");
    if (LANGS.includes(fromUrl)) {
      saveLang(fromUrl);
      return fromUrl;
    }
    const saved = savedLang();
    if (LANGS.includes(saved)) return saved;
    for (const l of navigator.languages || [navigator.language]) {
      const base = (l || "").slice(0, 2).toLowerCase();
      if (LANGS.includes(base)) return base;
    }
    return "en";
  }

  async function loadDict(l) {
    const resp = await fetch(`i18n/${l}.json`);
    if (!resp.ok) throw new Error(`i18n/${l}.json: ${resp.status}`);
    return resp.json();
  }

  function lookup(key) {
    return dict[key] ?? fallback[key];
  }

  function t(key, params = {}) {
    if ("count" in params) {
      const rule = new Intl.PluralRules(LOCALES[lang]).select(params.count);
      key = lookup(`${key}.${rule}`) !== undefined ? `${key}.${rule}` : `${key}.other`;
    }
    const text = lookup(key) ?? key;
    return text.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match));
  }

  // Leaves an element's built-in English as is if a key is missing.
  function apply() {
    document.documentElement.lang = lang;
    const titleKey = document.documentElement.dataset.i18nTitle;
    if (titleKey && lookup(titleKey) !== undefined) document.title = t(titleKey);
    const set = (attr, fn) =>
      document.querySelectorAll(`[${attr}]`).forEach((el) => {
        const key = el.getAttribute(attr);
        if (lookup(key) !== undefined) fn(el, t(key));
      });
    set("data-i18n", (el, s) => (el.textContent = s));
    set("data-i18n-html", (el, s) => (el.innerHTML = s));
    set("data-i18n-placeholder", (el, s) => (el.placeholder = s));
    document.querySelectorAll("[data-i18n-block]").forEach((el) => (el.hidden = el.lang !== lang));
    document.querySelectorAll(".lang-picker [data-lang]").forEach((btn) =>
      btn.setAttribute("aria-pressed", String(btn.dataset.lang === lang))
    );
  }

  async function load(l) {
    try {
      dict = l === "en" ? fallback : await loadDict(l);
      lang = l;
    } catch {
      dict = fallback;
      lang = "en";
    }
  }

  // Resolves once the dictionaries are loaded and the page is translated.
  const ready = (async () => {
    try {
      fallback = await loadDict("en");
      await load(lang);
    } catch {
      lang = "en"; // keep the English built into the HTML
    }
    apply();
    document.documentElement.classList.remove("i18n-pending");
  })();

  // Pages re-render their dynamic content on this event.
  async function setLang(l) {
    if (!LANGS.includes(l) || l === lang) return;
    saveLang(l);
    await load(l);
    // Drop a ?lang= that would otherwise win on the next visit.
    const url = new URL(location.href);
    if (url.searchParams.has("lang")) {
      url.searchParams.delete("lang");
      history.replaceState(null, "", url);
    }
    apply();
    window.dispatchEvent(new Event("i18n:change"));
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".lang-picker [data-lang]");
    if (btn) setLang(btn.dataset.lang);
  });

  // Never leave the page hidden if loading hangs.
  setTimeout(() => document.documentElement.classList.remove("i18n-pending"), 3000);

  return {
    t,
    ready,
    lang: () => lang,
    locale: () => LOCALES[lang],
  };
})();

const t = I18N.t;
