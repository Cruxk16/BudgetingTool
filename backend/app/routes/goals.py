from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import Goal, GoalContribution, get_session
from ..services import budget, goals

router = APIRouter(prefix="/api")


class GoalBody(BaseModel):
    name: str
    price: float
    saved_so_far: float = 0
    monthly_contribution: Optional[float] = None
    target_date: Optional[date] = None
    priority: int = 1


class ContribBody(BaseModel):
    amount: float
    date: Optional[date] = None


@router.get("/goals")
def list_goals(session: Session = Depends(get_session)):
    b = budget.current_budget(session)
    surplus = b["totals"]["surplus"]
    return {"surplus": surplus, "goals": goals.plan_all(session, surplus, b["lines"])}


@router.post("/goals")
def create_goal(body: GoalBody, session: Session = Depends(get_session)):
    g = Goal(**body.model_dump())
    session.add(g)
    session.commit()
    return list_goals(session)


@router.put("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalBody, session: Session = Depends(get_session)):
    g = session.get(Goal, goal_id)
    if not g:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(g, k, v)
    session.add(g)
    session.commit()
    return list_goals(session)


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: int, session: Session = Depends(get_session)):
    g = session.get(Goal, goal_id)
    if g:
        for c in session.exec(select(GoalContribution).where(GoalContribution.goal_id == goal_id)):
            session.delete(c)
        session.delete(g)
        session.commit()
    return list_goals(session)


@router.post("/goals/{goal_id}/contributions")
def add_contribution(goal_id: int, body: ContribBody, session: Session = Depends(get_session)):
    session.add(GoalContribution(goal_id=goal_id, amount=body.amount, date=body.date or date.today()))
    session.commit()
    return list_goals(session)
