"""
A driver's areas (see areas.py):

  GET /api/areas           -- the signed-in user's areas, and the limits
  POST /api/areas          -- save an area: a place and a radius
  PUT /api/areas/{id}      -- change an area's radius
  DELETE /api/areas/{id}   -- remove an area
  GET /api/areas/requests  -- the open ride requests in the user's areas
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

import areas
import notify
from api import Member, Writer, api_error

router = APIRouter()


def _one_of_the_options(radius_km: int) -> int:
    if radius_km not in areas.RADIUS_OPTIONS_KM:
        raise ValueError(f"radius_km must be one of {list(areas.RADIUS_OPTIONS_KM)}")
    return radius_km


class AreaPlace(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class AreaCreate(BaseModel):
    place: AreaPlace
    radius_km: int = areas.DEFAULT_RADIUS_KM

    _radius = field_validator("radius_km")(_one_of_the_options)


class AreaUpdate(BaseModel):
    radius_km: int

    _radius = field_validator("radius_km")(_one_of_the_options)


def _not_found():
    # Also for someone else's area: whether an id exists is nobody's business.
    return api_error(404, "area_not_found", "Area not found")


@router.get("/api/areas")
def get_areas(user: Member):
    return {
        "areas": areas.list_for(user["id"]),
        "max_areas": areas.MAX_AREAS,
        "radius_options_km": list(areas.RADIUS_OPTIONS_KM),
        "default_radius_km": areas.DEFAULT_RADIUS_KM,
    }


@router.post("/api/areas")
def add_area(payload: AreaCreate, user: Writer):
    area = areas.add(user["id"], payload.place.name, payload.place.lat, payload.place.lon, payload.radius_km)
    if area is None:
        raise api_error(400, "too_many_areas", f"At most {areas.MAX_AREAS} areas can be saved")
    return area


@router.put("/api/areas/{area_id}")
def update_area(area_id: int, payload: AreaUpdate, user: Writer):
    area = areas.set_radius(area_id, user["id"], payload.radius_km)
    if area is None:
        raise _not_found()
    return area


@router.delete("/api/areas/{area_id}")
def remove_area(area_id: int, user: Writer):
    if not areas.remove(area_id, user["id"]):
        raise _not_found()
    return {"deleted": area_id}


@router.get("/api/areas/requests")
def get_requests_in_areas(user: Member):
    # Viewing as a demo driver: no areas, so no real passenger's request.
    return notify.requests_in_areas(user)
