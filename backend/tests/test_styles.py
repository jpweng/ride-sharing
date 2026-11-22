"""
The stylesheets: frontend/style.css (base, layout, shared components) and
one file per feature in frontend/styles/, linked right after style.css by
the pages that use them, so their rules keep the same cascade order.
"""
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PAGES = ["index.html", "privacy.html", "conduct.html", "offline.html", "contact.html"]


def _own_stylesheets(page):
    html = (FRONTEND / page).read_text()
    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    return [href for href in hrefs if not href.startswith(("https://", "http://", "//"))]


@pytest.mark.parametrize("page", PAGES)
def test_style_css_first_then_feature_styles(page):
    sheets = _own_stylesheets(page)
    assert sheets[0] == "style.css"
    assert all(href.startswith("styles/") for href in sheets[1:]), sheets
    for href in sheets:
        assert (FRONTEND / href).is_file(), href


def test_every_feature_stylesheet_is_linked():
    linked = {href for page in PAGES for href in _own_stylesheets(page)}
    for path in (FRONTEND / "styles").glob("*.css"):
        assert f"styles/{path.name}" in linked, path.name


def test_pages_with_pwa_js_have_its_styles():
    for page in PAGES:
        html = (FRONTEND / page).read_text()
        if '<script src="pwa.js"></script>' in html:
            assert "styles/pwa.css" in _own_stylesheets(page), page

