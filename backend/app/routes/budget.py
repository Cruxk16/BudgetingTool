from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import Budget, get_session
from ..services import analyzer, budget
from ..services.common import month_key

router = APIRouter(prefix="/api")


class LineBody(BaseModel):
    amount: float
    category: Optional[str] = None
    kind: Optional[str] = None


@router.get("/budget")
def get_budget(session: Session = Depends(get_session)):
    return budget.current_budget(session)


@router.post("/budget/generate")
def generate(months: int = 6, session: Session = Depends(get_session)):
    gen = budget.generate(session, n_months=months)
    budget.save(session, month_key(date.today()), gen["lines"])
    return budget.current_budget(session)


@router.post("/budget/baseline")
def save_baseline(session: Session = Depends(get_session)):
    """Make this month's plan the baseline that future months start from."""
    budget.save(session, budget.BASELINE, budget.saved_lines(session, month_key(date.today())))
    return budget.current_budget(session)


@router.post("/budget/reset")
def reset(session: Session = Depends(get_session)):
    budget.reset_to_baseline(session, month_key(date.today()))
    return budget.current_budget(session)


@router.post("/budget/lines")
def add_line(body: LineBody, session: Session = Depends(get_session)):
    if not body.category:
        raise HTTPException(400, "category required")
    session.add(Budget(month=month_key(date.today()), category=body.category, amount=body.amount,
                       kind=body.kind or "variable"))
    session.commit()
    return budget.current_budget(session)


@router.put("/budget/lines/{line_id}")
def edit_line(line_id: int, body: LineBody, session: Session = Depends(get_session)):
    b = session.get(Budget, line_id)
    if not b:
        raise HTTPException(404)
    b.amount = body.amount
    session.add(b)
    session.commit()
    return budget.current_budget(session)


@router.delete("/budget/lines/{line_id}")
def del_line(line_id: int, session: Session = Depends(get_session)):
    b = session.get(Budget, line_id)
    if b:
        session.delete(b)
        session.commit()
    return budget.current_budget(session)


@router.get("/progress")
def progress(session: Session = Depends(get_session)):
    return budget.progress(session)


@router.get("/insights")
def insights(month: Optional[str] = None, session: Session = Depends(get_session)):
    month = month or month_key(date.today())
    return {
        "month": month,
        "overview": analyzer.overview(session),
        "categories": analyzer.by_category(session, month),
        "merchants": analyzer.merchants(session),
        "cards": analyzer.by_card(session, month),
        "mom": analyzer.month_over_month(session, month),
        "subscriptions": analyzer.subscriptions(session),
        "unusual": analyzer.unusual(session),
    }
