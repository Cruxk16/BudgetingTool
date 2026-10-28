from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.db import Account, Transaction, engine
from app.main import app
from app.services import budget, categorize
from app.services.common import load_expenses

TODAY = date(2026, 10, 28)


@pytest.fixture()
def client():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def seed(session, rows):
    acct = Account(institution="B", name="Checking", type="checking", role="income_bills")
    session.add(acct)
    session.commit()
    for i, (name, amount) in enumerate(rows):
        session.add(Transaction(account_id=acct.id, dedupe_key=f"k{i}", date=date(2026, 10, 10 + i), amount=amount, name=name))
    session.commit()


def rule(client, pattern, category, incoming_only=True):
    assert client.post("/api/rules", json={"match_type": "contains", "pattern": pattern, "category": category,
                                           "incoming_only": incoming_only}).status_code == 200


def test_incoming_only_rule_leaves_outgoing_alone(client):
    with Session(engine) as s:
        seed(s, [("Venmo", -40.0), ("Venmo", 25.0)])
    rule(client, "venmo", "Reimbursement")
    with Session(engine) as s:
        by_amount = {t.amount: t for t in s.exec(select(Transaction))}
    assert by_amount[-40.0].category == "Reimbursement"
    assert by_amount[25.0].category != "Reimbursement"


def test_deposit_in_spending_category_nets_against_it(client):
    with Session(engine) as s:
        seed(s, [("RECURRING TRANSFER FROM ASHLEY V", -116.96), ("Navient", 300.0), ("PAYROLL", -1000.0)])
    rule(client, "from ashley", "Student Loans")
    with Session(engine) as s:
        loans = sum(e.amount for e in load_expenses(s) if e.category == "Student Loans")
        assert loans == pytest.approx(183.04)
        assert not [e for e in load_expenses(s) if e.category == "Income"]  # paycheck stays out


def test_reimbursement_and_extra_income_raise_allowance(client):
    with Session(engine) as s:
        seed(s, [("Dinner", 80.0), ("Venmo", -60.0), ("eBay", -50.0)])
        categorize.apply_all(s)
        before = budget.progress(s, TODAY)["free"]
    rule(client, "venmo", "Reimbursement")
    rule(client, "ebay", "Extra Income")
    with Session(engine) as s:
        after = budget.progress(s, TODAY)["free"]
    assert after["reimbursed"] == 60 and after["extra_income"] == 50
    assert after["left"] - before["left"] == pytest.approx(110)


def test_reimbursement_cannot_exceed_unplanned_spending(client):
    with Session(engine) as s:
        seed(s, [("Dinner", 20.0), ("Venmo", -60.0)])
    rule(client, "venmo", "Reimbursement")
    with Session(engine) as s:
        assert budget.progress(s, TODAY)["free"]["reimbursed"] == 20


def test_recategorizing_a_transfer_unflags_it(client):
    with Session(engine) as s:
        seed(s, [("Venmo", -40.0)])
        t = s.exec(select(Transaction)).first()
        t.is_transfer = True
        s.add(t)
        s.commit()
        tid = t.id
    assert client.patch(f"/api/transactions/{tid}", json={"category": "Dining"}).json()["is_transfer"] is False
    assert client.patch(f"/api/transactions/{tid}", json={"category": "Transfer"}).json()["is_transfer"] is True
