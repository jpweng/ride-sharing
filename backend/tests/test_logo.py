"""
Tests for the logo: frontend/icons/logo.svg is served as an image, is part
of the offline copy, and is what the pages' header shows next to the name.
No network; FastAPI's TestClient is used without its lifespan.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from routes import pwa

FRONTEND = Path(pwa.FRONTEND_DIR)
STYLESHEETS = sorted([FRONTEND / "style.css", *FRONTEND.glob("styles/*.css")])
CSS_URL = re.compile(r"url\(\s*[\"']?([^\"')]+)[\"']?\s*\)")


@pytest.fixture
def client():
    return TestClient(main.app)


def test_logo_is_served_as_svg(client):
    resp = client.get("/icons/logo.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    root = ET.fromstring(resp.text)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    # Square, so it scales to the header's mark and to any icon size.
    _, _, width, height = root.attrib["viewBox"].split()
    assert width == height


def test_logo_is_in_the_offline_copy():
    assert "icons/logo.svg" in pwa.shell_files()


def test_header_shows_the_logo():
    css = (FRONTEND / "style.css").read_text()
    rule = re.search(r"\.wordmark::before\s*\{(.*?)\}", css, re.S)
    assert rule, "style.css must draw the logo before the wordmark"
    assert CSS_URL.search(rule.group(1)).group(1) == "icons/logo.svg"
    # Every page has the wordmark in its header and loads style.css.
    for page in FRONTEND.glob("*.html"):
        html = page.read_text()
        assert 'class="wordmark"' in html, page.name
        assert '<link rel="stylesheet" href="style.css" />' in html, page.name


@pytest.mark.parametrize("stylesheet", STYLESHEETS, ids=lambda path: path.name)
def test_files_named_in_stylesheets_exist(stylesheet):
    for url in CSS_URL.findall(stylesheet.read_text()):
        if url.startswith(("data:", "https://", "http://", "//", "#")):
            continue
        assert (stylesheet.parent / url.split("?")[0].split("#")[0]).is_file(), f"{stylesheet.name}: {url}"
