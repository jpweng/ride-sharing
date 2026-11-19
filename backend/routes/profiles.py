"""
Profiles (see profiles.py):

  GET, PUT /api/profile          -- one's own profile
  PUT, DELETE /api/profile/photo -- one's photo (a JPEG in the request body)
  GET /api/profiles/{id}         -- someone else's, if one may see it
  GET /api/profiles/{id}/photo   -- their photo, likewise
"""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

import photos
import profiles
from api import Member, Writer, api_error

router = APIRouter()


class ProfileUpdate(BaseModel):
    about: str | None = Field(default=None, max_length=profiles.MAX_ABOUT_CHARS)
    talkative: bool | None = None
    sleeps: bool | None = None
    smoker: bool | None = None
    pets: bool | None = None
    music: bool | None = None
    car_make: str | None = Field(default=None, max_length=profiles.MAX_CAR_CHARS)
    car_model: str | None = Field(default=None, max_length=profiles.MAX_CAR_CHARS)
    car_year: int | None = Field(default=None, ge=profiles.MIN_CAR_YEAR, le=2100)
    car_color: str | None = Field(default=None, max_length=profiles.MAX_CAR_CHARS)


def _own(user: dict) -> dict:
    return {**profiles.own(user), "photos_enabled": photos.enabled()}


def _not_found():
    # Also for a profile that isn't the user's to see: whether someone has
    # an account is nobody else's business.
    return api_error(404, "profile_not_found", "Profile not found")


@router.get("/api/profile")
def get_own_profile(user: Member):
    return _own(user)


@router.put("/api/profile")
def save_own_profile(payload: ProfileUpdate, user: Writer):
    profiles.save(user, payload.model_dump())
    return _own(user)


@router.put("/api/profile/photo")
async def set_own_photo(request: Request, user: Writer):
    if not photos.enabled():
        raise api_error(503, "photos_unavailable", "Photos can't be stored at the moment")
    # Read by hand: one small file as the whole body, no form.
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > photos.MAX_BYTES:
            raise api_error(413, "photo_too_large", "The photo is too large")
    if not photos.is_jpeg(data):
        raise api_error(400, "photo_invalid", "The photo must be a JPEG image")
    try:
        profiles.set_photo(user, data)
    except photos.PhotoError:
        photos.logger.exception("Storing a photo failed")
        raise api_error(503, "photos_unavailable", "Photos can't be stored at the moment")
    return _own(user)


@router.delete("/api/profile/photo")
def remove_own_photo(user: Writer):
    profiles.remove_photo(user["id"])
    return _own(user)


@router.get("/api/profiles/{user_id}")
def get_profile(user_id: int, user: Member):
    profile = profiles.visible(user, user_id)
    if profile is None:
        raise _not_found()
    return profile


@router.get("/api/profiles/{user_id}/photo")
def get_photo(user_id: int, user: Member):
    if profiles.visible(user, user_id) is None:
        raise _not_found()
    try:
        data = profiles.photo(user_id)
    except photos.PhotoError:
        photos.logger.exception("Reading a photo failed")
        data = None
    if data is None:
        raise _not_found()
    # Private: only for this viewer's browser, never a shared cache.
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
