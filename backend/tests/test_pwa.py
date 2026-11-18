"""
Tests for the installable app (PWA) and the Android app's Digital Asset
Links: how the manifest, the service worker and assetlinks.json are served,
and that the files they refer to exist. No network; FastAPI's TestClient is
used without its lifespan, so the maintenance task (which calls OSRM) never
starts.
"""
import json
import re
import shutil
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from routes import pwa

FRONTEND = Path(pwa.FRONTEND_DIR)
FINGERPRINT_A = ":".join(["AB"] * 32)
FINGERPRINT_B = ":".join(["0f"] * 32)


@pytest.fixture
def client():
    return TestClient(main.app)


def _png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def _sw_list(name, source=None):
    """A list from sw.js as the server serves it (the shell is filled in then)."""
    if source is None:
        source = TestClient(main.app).get("/sw.js").text
    block = re.search(rf"const {name} = \[(.*?)\];", source, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


def _sw_shell():
    return _sw_list("SHELL")


# --- manifest -------------------------------------------------------------

def test_manifest_content_type(client):
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")


def test_manifest_content(client):
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["name"] == "Ride and Rail"
    assert manifest["short_name"]
    assert manifest["start_url"] == "/" and manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    css = (FRONTEND / "style.css").read_text()
    assert f"--accent: {manifest['theme_color']};" in css
    assert f"--bg: {manifest['background_color']};" in css
    sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in manifest["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes


def test_manifest_icons_served_at_their_size(client):
    manifest = client.get("/manifest.webmanifest").json()
    for icon in manifest["icons"]:
        resp = client.get(icon["src"])
        assert resp.status_code == 200, icon["src"]
        assert resp.headers["content-type"] == "image/png"
        width, height = _png_size(resp.content)
        assert f"{width}x{height}" == icon["sizes"], icon["src"]


def test_apple_touch_icon():
    width, height = _png_size((FRONTEND / "icons" / "apple-touch-icon.png").read_bytes())
    assert (width, height) == (180, 180)


@pytest.mark.parametrize("page", ["index.html", "privacy.html", "conduct.html"])
def test_pages_are_installable(page):
    html = (FRONTEND / page).read_text()
    for tag in [
        '<link rel="manifest" href="manifest.webmanifest" />',
        '<link rel="apple-touch-icon" href="icons/apple-touch-icon.png" />',
        '<meta name="apple-mobile-web-app-capable" content="yes" />',
        '<meta name="apple-mobile-web-app-status-bar-style"',
        '<script src="pwa.js"></script>',
    ]:
        assert tag in html, tag


# --- service worker -------------------------------------------------------

def test_service_worker_headers(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    assert resp.headers["cache-control"] == "no-cache"


def test_service_worker_is_versioned(client):
    source = client.get("/sw.js").text
    assert pwa.SW_VERSION_LINE not in source
    assert f'const VERSION = "{pwa.frontend_version()}";' in source


def test_source_has_the_lines_the_server_fills_in():
    # The source stays valid JavaScript on its own (an empty shell).
    source = (FRONTEND / "sw.js").read_text()
    assert pwa.SW_VERSION_LINE in source
    assert pwa.SW_SHELL_LINE in source
    assert _sw_list("SHELL", source) == []
    served = TestClient(main.app).get("/sw.js").text
    assert pwa.SW_SHELL_LINE not in served


def test_version_changes_with_any_frontend_file(tmp_path, monkeypatch):
    copy = tmp_path / "frontend"
    shutil.copytree(FRONTEND, copy)
    monkeypatch.setattr(pwa, "FRONTEND_DIR", str(copy))
    before = pwa.frontend_version()
    assert pwa.frontend_version() == before
    (copy / "style.css").write_text((copy / "style.css").read_text() + "\n/* changed */\n")
    assert pwa.frontend_version() != before


def test_shell_files_exist(client):
    shell = _sw_shell()
    assert len(shell) == len(set(shell))
    for path in shell:
        # Only our own files: a third-party file in the shell would make
        # every update depend on that site being reachable.
        assert not path.startswith(("https://", "http://", "//")), path
        assert client.get(f"/{path}").status_code == 200, path


def test_shell_is_every_frontend_file_the_browser_uses():
    shell = set(_sw_shell())
    # Every page, script, stylesheet and icon...
    for pattern in ("*.html", "*.js", "*.css", "styles/*.css", "icons/*.png", "*.webmanifest"):
        for path in FRONTEND.glob(pattern):
            if path.name != "sw.js":
                assert path.relative_to(FRONTEND).as_posix() in shell, path
    # ...the merged translations rather than the files they come from...
    assert {f"i18n/{lang}.json" for lang in ("en", "de", "fr", "it")} <= shell
    assert not [path for path in shell if path.startswith(tuple(f"i18n/{lang}/" for lang in ("en", "de", "fr", "it")))]
    # ...and nothing browsers don't use.
    assert "sw.js" not in shell
    assert not [path for path in shell if path.endswith((".py", ".md"))]


def test_new_frontend_file_joins_the_shell(tmp_path, monkeypatch):
    copy = tmp_path / "frontend"
    shutil.copytree(FRONTEND, copy)
    monkeypatch.setattr(pwa, "FRONTEND_DIR", str(copy))
    (copy / "styles" / "new-feature.css").write_text("/* new */\n")
    (copy / "README.md").write_text("notes\n")
    shell = pwa.shell_files()
    assert "styles/new-feature.css" in shell
    assert "README.md" not in shell


def test_libs_are_the_leaflet_index_html_loads():
    index = (FRONTEND / "index.html").read_text()
    loaded = set(re.findall(r'"(https://unpkg\.com/[^"]+)"', index))
    assert loaded
    assert loaded == set(_sw_list("LIBS"))


def test_shell_is_network_first():
    source = (FRONTEND / "sw.js").read_text()
    assert 'fetch(request, { cache: "no-cache" })' in source


def test_shell_covers_offline_page_needs():
    shell = set(_sw_shell())
    assert {"offline.html", "style.css", "i18n.js", "i18n/en.json", "i18n/de.json", "i18n/fr.json", "i18n/it.json"} <= shell
    for href in re.findall(r'(?:href|src)="([^"]+)"', (FRONTEND / "offline.html").read_text()):
        assert href in ("", "./") or href in shell, href


# --- Digital Asset Links --------------------------------------------------

def test_assetlinks_empty_without_fingerprint(client, monkeypatch):
    monkeypatch.delenv("ANDROID_CERT_SHA256", raising=False)
    resp = client.get("/.well-known/assetlinks.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.json() == []


def test_assetlinks_empty_when_blank(client, monkeypatch):
    monkeypatch.setenv("ANDROID_CERT_SHA256", " , ")
    assert client.get("/.well-known/assetlinks.json").json() == []


def test_assetlinks_with_fingerprints(client, monkeypatch):
    monkeypatch.setenv("ANDROID_CERT_SHA256", f" {FINGERPRINT_A} ,{FINGERPRINT_B}, ")
    resp = client.get("/.well-known/assetlinks.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.json() == [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "ch.rideandrail.app",
                "sha256_cert_fingerprints": [FINGERPRINT_A, FINGERPRINT_B.upper()],
            },
        }
    ]


def test_assetlinks_ignores_malformed_fingerprints(client, monkeypatch):
    monkeypatch.setenv("ANDROID_CERT_SHA256", f"not-a-fingerprint,{FINGERPRINT_A}")
    links = client.get("/.well-known/assetlinks.json").json()
    assert links[0]["target"]["sha256_cert_fingerprints"] == [FINGERPRINT_A]


def test_twa_manifest_matches_web_manifest():
    twa = json.loads((FRONTEND.parent / "mobile" / "android" / "twa-manifest.json").read_text())
    web = json.loads((FRONTEND / "manifest.webmanifest").read_text())
    assert twa["packageId"] == pwa.ANDROID_PACKAGE_NAME
    assert twa["host"] == "rideandrail.ch"
    assert twa["name"] == web["name"]
    assert twa["themeColor"].lower() == web["theme_color"]
    assert twa["backgroundColor"].lower() == web["background_color"]
    assert twa["startUrl"] == web["start_url"]
    assert twa["webManifestUrl"] == "https://rideandrail.ch/manifest.webmanifest"
    for key in ("iconUrl", "maskableIconUrl"):
        path = twa[key].removeprefix("https://rideandrail.ch/")
        assert (FRONTEND / path).is_file(), key
