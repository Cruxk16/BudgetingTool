from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..services import ai

router = APIRouter(prefix="/api/ai")


class KeyBody(BaseModel):
    key: Optional[str] = None


class ModelBody(BaseModel):
    model: str


class Msg(BaseModel):
    role: str
    text: str


class AskBody(BaseModel):
    question: str
    history: list[Msg] = []


@router.get("/status")
def status(session: Session = Depends(get_session)):
    return {"configured": ai.api_key() is not None, "model": ai.current_model(session)}


@router.put("/key")
def set_key(body: KeyBody, session: Session = Depends(get_session)):
    ai.save_key(body.key)
    return status(session)


@router.get("/models")
def models(session: Session = Depends(get_session)):
    return ai.available_models(session)


@router.put("/model")
def set_model(body: ModelBody, session: Session = Depends(get_session)):
    if not body.model.strip():
        raise HTTPException(400, "Pick a model")
    ai.set_model(session, body.model)
    return status(session)


@router.post("/ask")
def ask(body: AskBody, session: Session = Depends(get_session)):
    if not body.question.strip():
        raise HTTPException(400, "Ask a question")
    try:
        return ai.ask(session, body.question.strip(), [m.model_dump() for m in body.history][-12:])
    except ai.AIError as e:
        raise HTTPException(e.status, str(e))
