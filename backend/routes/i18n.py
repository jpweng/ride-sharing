"""
GET /i18n/{lang}.json -- a language's translations as the browser loads
them, merged from frontend/i18n/<lang>/*.json (see translations.py). Comes
before the static files, which have no such file.
"""
import hashlib
import json

from fastapi import APIRouter, HTTPException, Request, Response

import translations

router = APIRouter()


@router.api_route("/i18n/{language}.json", methods=["GET", "HEAD"], include_in_schema=False)
def i18n_dictionary(language: str, request: Request):
    if language not in translations.LANGUAGES:
        raise HTTPException(404, "Not Found")
    # Read per request (a few small files), so it follows edits during
    # development. no-cache: browsers check for a new version every time,
    # and the ETag spares them the download when nothing changed.
    body = (json.dumps(translations.dictionary(language), ensure_ascii=False, indent=2) + "\n").encode()
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    sent = [tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")]
    if etag in sent:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)
