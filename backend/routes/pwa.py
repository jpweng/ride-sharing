"""
Installable app (PWA) and the Android app (a Trusted Web Activity):

  GET /sw.js                       -- the service worker, versioned by the
                                      frontend's content, with the list of
                                      the app's files (its shell).
  GET /.well-known/assetlinks.json -- Digital Asset Links for the Android
                                      app, from ANDROID_CERT_SHA256.
"""
import hashlib
import json
import logging
import mimetypes
import os
import re
from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

import api
import translations

logger = logging.getLogger("uvicorn.error")

router = APIRouter()

# The frontend files, a module attribute so tests can point it elsewhere.
FRONTEND_DIR = api.FRONTEND_DIR

# Not in every system's MIME table (e.g. slim Docker images).
mimetypes.add_type("application/manifest+json", ".webmanifest")

SW_VERSION_LINE = 'const VERSION = "dev";'
SW_SHELL_LINE = "const SHELL = [];"

# Frontend files browsers never ask for, kept out of the shell.
NOT_FOR_BROWSERS = {".py", ".md"}


def frontend_version() -> str:
    """A hash of every frontend file, which changes with each deploy that changes any of them."""
    digest = hashlib.sha256()
    root = Path(FRONTEND_DIR)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix != ".py":
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def shell_files() -> list[str]:
    """The service worker's shell, relative to the site root: every file
    under frontend/ that the browser uses (not sw.js itself, scripts such as
    icons/make_icons.py, READMEs or hidden files), with each language's
    translations as the browser loads them, i18n/<lang>.json, instead of
    the files they're merged from."""
    root = Path(FRONTEND_DIR)
    i18n_dirs = {Path("i18n") / language for language in translations.LANGUAGES}
    files = [f"i18n/{language}.json" for language in translations.LANGUAGES]
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or relative == Path("sw.js")
            or path.suffix in NOT_FOR_BROWSERS
            or path.name.upper().startswith("README")
            or any(part.startswith(".") or part == "__pycache__" for part in relative.parts)
            or relative.parent in i18n_dirs
        ):
            continue
        files.append(relative.as_posix())
    return sorted(files)


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    # The version names the service worker's cache: a new one makes browsers
    # install the new files and drop the old ones (see frontend/sw.js).
    # Computed per request (the files are small), so it also follows edits
    # during development. no-cache: browsers must always check for a new one.
    source = (Path(FRONTEND_DIR) / "sw.js").read_text()
    for line in (SW_VERSION_LINE, SW_SHELL_LINE):
        if line not in source:
            raise RuntimeError(f"frontend/sw.js must contain {line!r}")
    source = source.replace(SW_VERSION_LINE, f'const VERSION = "{frontend_version()}";')
    shell = "".join(f"  {json.dumps(path)},\n" for path in shell_files())
    source = source.replace(SW_SHELL_LINE, f"const SHELL = [\n{shell}];")
    return Response(source, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


ANDROID_PACKAGE_NAME = "ch.rideandrail.app"
SHA256_FINGERPRINT = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")


@router.get("/.well-known/assetlinks.json", include_in_schema=False)
def asset_links():
    """
    Digital Asset Links: lets the Android app (mobile/android/) open this
    site full screen, without a browser bar. ANDROID_CERT_SHA256 holds the
    SHA-256 fingerprints of the app's signing certificates, comma-separated
    (with Play App Signing: the app signing key's, from the Play Console).
    Unset, the list is empty and the app would show a browser bar.
    """
    fingerprints = []
    for value in os.environ.get("ANDROID_CERT_SHA256", "").split(","):
        value = value.strip().upper()
        if not value:
            continue
        if SHA256_FINGERPRINT.match(value):
            fingerprints.append(value)
        else:
            logger.warning("Ignoring malformed ANDROID_CERT_SHA256 entry %r", value)
    if not fingerprints:
        return JSONResponse([])
    return JSONResponse([
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": ANDROID_PACKAGE_NAME,
                "sha256_cert_fingerprints": fingerprints,
            },
        }
    ])
