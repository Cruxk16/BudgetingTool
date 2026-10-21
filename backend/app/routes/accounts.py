from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import Account, Transaction, get_session
from ..services import categorize, importer, pdf_import

router = APIRouter(prefix="/api")

DEFAULT_ROLE = {"checking": "income_bills", "savings": "savings", "credit": "spending"}


class AccountBody(BaseModel):
    institution: str
    name: str
    mask: Optional[str] = None
    type: str = "checking"
    role: Optional[str] = None


class RoleBody(BaseModel):
    role: str


def _out(session: Session, a: Account) -> dict:
    txns = session.exec(select(Transaction.date).where(Transaction.account_id == a.id)).all()
    return {**a.model_dump(), "transaction_count": len(txns),
            "first_date": min(txns).isoformat() if txns else None,
            "last_date": max(txns).isoformat() if txns else None}


@router.get("/accounts")
def accounts(session: Session = Depends(get_session)):
    return [_out(session, a) for a in session.exec(select(Account))]


@router.post("/accounts")
def create_account(body: AccountBody, session: Session = Depends(get_session)):
    if body.type not in DEFAULT_ROLE:
        raise HTTPException(400, "type must be checking, savings or credit")
    a = Account(**{**body.model_dump(), "role": body.role or DEFAULT_ROLE[body.type]})
    session.add(a)
    session.commit()
    session.refresh(a)
    return _out(session, a)


@router.patch("/accounts/{account_id}")
def set_role(account_id: int, body: RoleBody, session: Session = Depends(get_session)):
    if body.role not in ("income_bills", "spending", "savings", "ignore"):
        raise HTTPException(400, "bad role")
    a = session.get(Account, account_id)
    if not a:
        raise HTTPException(404)
    a.role = body.role
    session.add(a)
    session.commit()
    categorize.apply_all(session)
    return _out(session, a)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    a = session.get(Account, account_id)
    if not a:
        raise HTTPException(404)
    for t in session.exec(select(Transaction).where(Transaction.account_id == account_id)):
        session.delete(t)
    session.delete(a)
    session.commit()
    return {"ok": True}


class RowBody(BaseModel):
    date: date
    amount: float
    name: str
    source_category: Optional[str] = None


class CommitBody(BaseModel):
    rows: list[RowBody]


@router.post("/accounts/{account_id}/import/preview")
async def preview_import(account_id: int, file: UploadFile = File(...), sign: str = Form("auto"),
                         session: Session = Depends(get_session)):
    """Parse a CSV or PDF statement and return what would be imported, without saving anything."""
    a = session.get(Account, account_id)
    if not a:
        raise HTTPException(404)
    name = (file.filename or "").lower()
    raw = await file.read()
    try:
        if name.endswith(".pdf"):
            parsed = pdf_import.parse_pdf(raw, a.type)
        elif name.endswith((".csv", ".txt")):
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            parsed = importer.parse_csv(text, sign, a.type)
        else:
            raise HTTPException(400, "Upload a PDF statement or a CSV file.")
    except importer.ImportFailed as e:
        raise HTTPException(400, str(e))
    return {"rows": [{**p, "date": p["date"].isoformat()} for p in parsed]}


@router.post("/accounts/{account_id}/import")
def commit_import(account_id: int, body: CommitBody, session: Session = Depends(get_session)):
    a = session.get(Account, account_id)
    if not a:
        raise HTTPException(404)
    if not body.rows:
        raise HTTPException(400, "Nothing to import")
    return importer.import_rows(session, a, [r.model_dump() for r in body.rows])
