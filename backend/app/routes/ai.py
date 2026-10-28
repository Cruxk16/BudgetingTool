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
    provider: Optional[str] = None  # defaults to the selected provider


class ProviderBody(BaseModel):
    provider: str


class EffortBody(BaseModel):
    effort: str  # "default" or one of the levels /models lists for the current model
    provider: Optional[str] = None


class Msg(BaseModel):
    role: str
    text: str


class AskBody(BaseModel):
    question: str
    history: list[Msg] = []


@router.get("/status")
def status(session: Session = Depends(get_session)):
    provider, options = ai.current_provider(session), ai.providers()
    return {"configured": next(p["available"] for p in options if p["id"] == provider), "provider": provider,
            "model": ai.current_model(session, provider), "effort": ai.current_effort(session, provider),
            "providers": options}


@router.put("/provider")
def set_provider(body: ProviderBody, session: Session = Depends(get_session)):
    try:
        ai.set_provider(session, body.provider)
    except ai.AIError as e:
        raise HTTPException(e.status, str(e))
    return status(session)


@router.put("/key")
def set_key(body: KeyBody, session: Session = Depends(get_session)):
    ai.save_key(body.key)
    return status(session)


@router.get("/models")
def models(provider: Optional[str] = None, session: Session = Depends(get_session)):
    if provider is not None and provider not in ai.PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    return ai.available_models(session, provider)


@router.put("/model")
def set_model(body: ModelBody, session: Session = Depends(get_session)):
    if not body.model.strip():
        raise HTTPException(400, "Pick a model")
    if body.provider is not None and body.provider not in ai.PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    ai.set_model(session, body.model, body.provider)
    return status(session)


@router.put("/effort")
def set_effort(body: EffortBody, session: Session = Depends(get_session)):
    if body.provider is not None and body.provider not in ai.PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    try:
        ai.set_effort(session, body.effort, body.provider)
    except ai.AIError as e:
        raise HTTPException(e.status, str(e))
    return status(session)


@router.post("/ask")
def ask(body: AskBody, session: Session = Depends(get_session)):
    if not body.question.strip():
        raise HTTPException(400, "Ask a question")
    try:
        return ai.ask(session, body.question.strip(), [m.model_dump() for m in body.history][-12:])
    except ai.AIError as e:
        raise HTTPException(e.status, str(e))
