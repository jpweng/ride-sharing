"""
The two parts of a search that wait for the timetable service, each on its
own, for a page that shows the drives first (POST /api/match with
"drives_only") and fills in the rest as it arrives:

  POST /api/match/transit   -- public transport for the whole trip:
                               {"transit_option": ...}, as /api/match gives it.
  POST /api/match/combined  -- a ride for part of the way and public
                               transport for the rest (combined.py):
                               {"combined_matches": [...]}, as /api/match
                               gives them. "exclude_ids": the drives the
                               search found for the whole trip.

Same body as /api/match, same access (public; who drives is for signed-in
users), same 400 "invalid_time". Asked for at the same time, the two ask
the timetable service once for what they share (transit.py's cache).
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from api import current_session
from routes import match

router = APIRouter()


class CombinedRequest(match.MatchRequest):
    # The drives /api/match found for the whole trip: not proposed again for
    # a part of it, and next to enough of them only drives that can be booked
    # are (combined.py).
    exclude_ids: list[int] = Field(default_factory=list, max_length=100)


@router.post("/api/match/transit")
def transit_part(payload: match.MatchRequest):
    match.check_time(payload)
    return {"transit_option": match.transit_alone(payload)}


@router.post("/api/match/combined")
def combined_part(payload: CombinedRequest, found: Annotated[dict | None, Depends(current_session)] = None):
    match.check_time(payload)
    # Public transport alone, to compare the trips with: looked up meanwhile.
    alone = match._pool.submit(match.transit_alone, payload)
    return {"combined_matches": match.combined_trips(payload, found, set(payload.exclude_ids), alone.result)}
