"""
The contact form (see contact.py, the module these routes call, and
docs/contact.md):

  GET  /api/contact -- whether the form is switched on
  POST /api/contact -- email a message to the team
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

import auth
import contact
from api import current_session

router = APIRouter()


class ContactMessage(BaseModel):
    name: str = Field(max_length=200)
    email: str = Field(max_length=auth.MAX_EMAIL_LENGTH)
    message: str = Field(max_length=contact.MAX_MESSAGE_CHARS)
    language: str = "en"  # of the page the visitor wrote on
    website: str = Field(default="", max_length=200)  # hidden from people: filled in by robots


@router.get("/api/contact")
def contact_status():
    return {"enabled": contact.enabled()}


@router.post("/api/contact")
def send_message(payload: ContactMessage, request: Request,
                 found: Annotated[dict | None, Depends(current_session)]):
    # Which account wrote, when signed in with one's own (not as a demo
    # driver): the team can then tell a request about an account comes from
    # its owner.
    account = None
    if found and not found["session"]["is_demo"] and found["user"]["email"]:
        account = {"id": found["user"]["id"], "email": found["user"]["email"]}
    contact.send(payload.name, payload.email, payload.message, payload.language, payload.website,
                 ip=request.client.host if request.client else None, account=account)
    return {"ok": True}
