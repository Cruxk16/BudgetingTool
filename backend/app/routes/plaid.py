from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..services import plaid

router = APIRouter(prefix="/api/plaid")


class CredsBody(BaseModel):
    client_id: Optional[str] = None  # nulls clear the saved credentials
    secret: Optional[str] = None
    env: str = "sandbox"


class LinkTokenBody(BaseModel):
    item_id: Optional[int] = None  # reconnect an existing login instead of adding a bank


class ExchangeBody(BaseModel):
    public_token: str
    institution: str = ""


def _run(fn, *args):
    try:
        return fn(*args)
    except plaid.PlaidError as e:
        raise HTTPException(e.status, str(e))


@router.get("/status")
def status(session: Session = Depends(get_session)):
    return plaid.status(session)


@router.put("/credentials")
def set_credentials(body: CredsBody, session: Session = Depends(get_session)):
    _run(plaid.save_credentials, body.client_id, body.secret, body.env)
    return plaid.status(session)


@router.post("/link_token")
def link_token(body: LinkTokenBody, session: Session = Depends(get_session)):
    return {"link_token": _run(plaid.link_token, session, body.item_id)}


@router.post("/exchange")
def exchange(body: ExchangeBody, session: Session = Depends(get_session)):
    return _run(plaid.link, session, body.public_token, body.institution)


@router.post("/sync")
def sync(session: Session = Depends(get_session)):
    return plaid.sync_all(session)


@router.delete("/items/{item_id}")
def unlink(item_id: int, session: Session = Depends(get_session)):
    _run(plaid.unlink, session, item_id)
    return plaid.status(session)
