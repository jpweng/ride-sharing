"""
Makes the pictures the stores ask for, from the site as it is: phone screenshots in the site's
four languages, at Google Play's and the App Store's sizes, and Google Play's feature graphic.

    uv run --with playwright python mobile/make_store_graphics.py [--site URL] [--out FOLDER]

It opens the site in a headless Chromium the size of a phone (the apps show the same pages), runs
one search per language and store, and saves what the screen shows. By default the site is
https://rideandrail.ch: the searches are a visitor's, without an account. The pictures are not
committed (mobile/graphics/ is ignored by git): make them again when the site's look changes.

Chromium is Playwright's (`uv run --with playwright playwright install chromium`), or the one
named by the CHROME environment variable.

    <out>/play/feature-graphic.png            1024x500
    <out>/play/<lang>/1-search.png ...        1080x1920
    <out>/app-store/<lang>/1-search.png ...   1320x2868 (6.9-inch iPhone)
"""
import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
LANGUAGES = ("en", "de", "fr", "it")
# Viewport in CSS pixels and the scale that gives the store's size.
STORES = {
    "play": {"width": 360, "height": 640, "scale": 3},
    "app-store": {"width": 440, "height": 956, "scale": 3},
}
# The trip searched for: two towns far enough apart for a ride and for a train.
TRIP = {
    "origin_name": "Lausanne", "origin_lat": 46.5197, "origin_lon": 6.6323,
    "destination_name": "Bern", "destination_lat": 46.9480, "destination_lon": 7.4474,
}
TAGLINE_KEY = "header.subtitle"
NAVY, WHITE, MUTED = "#1e2235", "#ffffff", "#c9cede"


def feature_graphic(browser, out: Path) -> None:
    """Google Play's banner: the logo, the name and the tagline, on the logo's navy."""
    import json
    logo = (REPO / "frontend/icons/logo.svg").read_text()
    tagline = json.loads((REPO / "frontend/i18n/en/header.json").read_text())[TAGLINE_KEY]
    html = f"""<!doctype html><meta charset="utf-8"><style>
      body {{ margin: 0; width: 1024px; height: 500px; background: {NAVY}; display: flex; align-items: center;
              justify-content: center; gap: 44px; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
      svg {{ width: 200px; height: 200px; }}
      h1 {{ margin: 0; font-size: 84px; color: {WHITE}; letter-spacing: -1px; white-space: nowrap; }}
      h1 i {{ font-weight: 400; font-size: 60px; color: {MUTED}; }}
      p {{ margin: 14px 0 0; font-size: 38px; color: {MUTED}; }}
    </style><body>{logo}<div><h1>Ride <i>and</i> Rail</h1><p>{tagline}</p></div>"""
    page = browser.new_page(viewport={"width": 1024, "height": 500})
    page.set_content(html)
    page.screenshot(path=out / "feature-graphic.png")
    page.close()


def screenshots(browser, site: str, store: str, language: str, out: Path) -> None:
    size = STORES[store]
    context = browser.new_context(
        viewport={"width": size["width"], "height": size["height"]}, device_scale_factor=size["scale"],
        is_mobile=True, has_touch=True, locale=f"{language}-CH", timezone_id="Europe/Zurich",
    )
    page = context.new_page()
    page.goto(f"{site}/?lang={language}", wait_until="networkidle")

    # 1. The search page as it opens.
    page.screenshot(path=out / "1-search.png")

    # 2. and 3. A search for tomorrow morning: the form with its first results, then the results.
    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    page.evaluate("trip => repeatSearch(trip)", TRIP)
    page.wait_for_selector("#ride-when:not([hidden])")
    page.evaluate(
        """iso => { setDateTime("ride", new Date(iso));
                    document.getElementById("ride-hour").dispatchEvent(new Event("change", { bubbles: true })); }""",
        tomorrow.isoformat(),
    )
    page.wait_for_selector("#ride-results:not([hidden])", timeout=30_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    for name, selector in (("2-trip.png", "#page-ride h2, #page-ride .page-title"), ("3-results.png", "#ride-results")):
        page.evaluate(
            """selector => { const el = document.querySelector(selector) || document.getElementById("page-ride");
                             el.scrollIntoView({ block: "start" }); }""",
            selector,
        )
        page.wait_for_timeout(400)
        page.screenshot(path=out / name)

    # 4. What the service is: the About page.
    page.goto(f"{site}/about.html?lang={language}", wait_until="networkidle")
    page.evaluate("""() => document.querySelector("article:not([hidden])").scrollIntoView({ block: "start" })""")
    page.wait_for_timeout(300)
    page.screenshot(path=out / "4-about.png")
    context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--site", default="https://rideandrail.ch", help="the site to take the pictures of")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "graphics")
    args = parser.parse_args()
    site = args.site.rstrip("/")

    def launch(playwright, language):
        # The language of the browser's own environment decides how a date field is written
        # (25.11.2026, not 11/25/2026); the page's locale alone doesn't.
        locale = f"{language}_CH.UTF-8"
        return playwright.chromium.launch(
            executable_path=os.environ.get("CHROME") or None,
            env={**os.environ, "LANGUAGE": locale.split(".")[0], "LANG": locale, "LC_ALL": locale},
        )

    with sync_playwright() as playwright:
        (args.out / "play").mkdir(parents=True, exist_ok=True)
        for language in LANGUAGES:
            browser = launch(playwright, language)
            if language == LANGUAGES[0]:
                feature_graphic(browser, args.out / "play")
                print(f"wrote {args.out / 'play' / 'feature-graphic.png'}")
            for store in STORES:
                folder = args.out / store / language
                folder.mkdir(parents=True, exist_ok=True)
                screenshots(browser, site, store, language, folder)
                print(f"wrote {folder}/")
            browser.close()


if __name__ == "__main__":
    main()
