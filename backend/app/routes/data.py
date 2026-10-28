from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import Account, CategoryRule, Transaction, get_session, set_setting
from ..services import categorize
from ..services.common import merchant_key
from ..services.income import detect_income

router = APIRouter(prefix="/api")


class TxnPatch(BaseModel):
    category: Optional[str] = None
    is_transfer: Optional[bool] = None
    notes: Optional[str] = None
    apply_to_merchant: bool = False


class RuleBody(BaseModel):
    match_type: str = "merchant"
    pattern: str
    category: str = "Other"
    is_transfer: bool = False
    incoming_only: bool = False


class IncomeBody(BaseModel):
    monthly: Optional[float] = None  # null clears the override


@router.get("/categories")
def categories():
    return categorize.CATEGORIES


@router.get("/transactions")
def transactions(q: str = "", category: str = "", account_id: int = 0, start: Optional[date] = None,
                 end: Optional[date] = None, transfers: str = "all", limit: int = 200, offset: int = 0,
                 session: Session = Depends(get_session)):
    stmt = select(Transaction, Account).where(Transaction.account_id == Account.id)
    if category:
        stmt = stmt.where(Transaction.category == category)
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    if start:
        stmt = stmt.where(Transaction.date >= start)
    if end:
        stmt = stmt.where(Transaction.date <= end)
    if transfers == "only":
        stmt = stmt.where(Transaction.is_transfer == True)  # noqa: E712
    elif transfers == "hide":
        stmt = stmt.where(Transaction.is_transfer == False)  # noqa: E712
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Transaction.name.ilike(like) | Transaction.merchant_name.ilike(like))
    rows = session.exec(stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).offset(offset).limit(limit)).all()
    return [{**t.model_dump(), "account": a.name, "mask": a.mask} for t, a in rows]


@router.patch("/transactions/{txn_id}")
def patch_txn(txn_id: int, body: TxnPatch, session: Session = Depends(get_session)):
    t = session.get(Transaction, txn_id)
    if not t:
        raise HTTPException(404)
    if body.category is not None:
        t.category = body.category
        t.is_transfer = body.category in categorize.TRANSFER_CATEGORIES  # re-filing out of Transfer un-flags it
    if body.is_transfer is not None:
        t.is_transfer = body.is_transfer
    if body.notes is not None:
        t.notes = body.notes
    t.manual = True
    session.add(t)
    if body.apply_to_merchant and (t.merchant_name or t.name):
        session.add(CategoryRule(match_type="merchant", pattern=merchant_key(t.merchant_name, t.name),
                                 category=t.category, is_transfer=t.is_transfer))
        session.commit()
        categorize.apply_all(session)
    session.commit()
    session.refresh(t)
    return t


@router.get("/rules")
def rules(session: Session = Depends(get_session)):
    return session.exec(select(CategoryRule)).all()


@router.post("/rules")
def add_rule(body: RuleBody, session: Session = Depends(get_session)):
    r = CategoryRule(**{**body.model_dump(), "pattern": body.pattern.lower()})
    session.add(r)
    session.commit()
    categorize.apply_all(session)
    return r


@router.delete("/rules/{rule_id}")
def del_rule(rule_id: int, session: Session = Depends(get_session)):
    r = session.get(CategoryRule, rule_id)
    if r:
        session.delete(r)
        session.commit()
        categorize.apply_all(session)
    return {"ok": True}


@router.get("/income")
def income(session: Session = Depends(get_session)):
    return detect_income(session)


@router.put("/income")
def set_income(body: IncomeBody, session: Session = Depends(get_session)):
    set_setting(session, "income_override", None if body.monthly is None else str(body.monthly))
    return detect_income(session)
