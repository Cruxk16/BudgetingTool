import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app import baseline
from app.db import engine
from app.main import app

BASELINE_FILE = Path(__file__).resolve().parents[2] / "data" / "baseline.json"


@pytest.fixture()
def session():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.mark.skipif(not BASELINE_FILE.exists(), reason="no personal baseline file")
def test_personal_baseline_totals(session):
    baseline.load(session, BASELINE_FILE)
    with TestClient(app) as c:
        b = c.get("/api/budget").json()
    assert b["income"]["monthly"] == 5950.60
    assert b["totals"]["expenses"] == pytest.approx(3712.25, abs=0.01)
    assert b["totals"]["surplus"] == pytest.approx(2238.35, abs=0.01)
    by = {l["category"]: l["amount"] for l in b["lines"]}
    assert by["Rent/Housing"] == 1425 and by["Phone/Internet"] == 122 and by["Subscriptions"] == pytest.approx(74.50)


def test_baseline_carries_into_month_and_resets(session, tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"income": 3000, "lines": [
        {"category": "Rent/Housing", "kind": "fixed", "items": [{"name": "Rent", "amount": 1000}]},
        {"category": "Groceries", "kind": "variable", "items": [{"name": "Food", "amount": 300}]}]}))
    baseline.load(session, f)
    with TestClient(app) as c:
        b = c.get("/api/budget").json()
        assert b["saved"] and b["totals"]["surplus"] == 1700
        line = next(l for l in b["lines"] if l["category"] == "Groceries")
        c.put(f"/api/budget/lines/{line['id']}", json={"amount": 500})
        assert c.get("/api/budget").json()["totals"]["surplus"] == 1500
        assert c.post("/api/budget/reset").json()["totals"]["surplus"] == 1700
        # unbudgeted category shows up in progress as over
        prog = c.get("/api/progress").json()
        assert {i["category"] for i in prog["items"]} == {"Rent/Housing", "Groceries"}


def test_payroll_line_rent_rule_and_free_money(session, tmp_path):
    from datetime import date
    from app.db import Account, Transaction
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"income": 3000, "rules": [
        {"match_type": "contains", "pattern": "example person", "category": "Rent/Housing"}], "lines": [
        {"category": "Rent/Housing", "kind": "fixed", "items": [{"name": "Rent", "amount": 1000}]},
        {"category": "Gym (payroll)", "kind": "payroll", "items": [{"name": "Gym", "amount": 2}]}]}))
    baseline.load(session, f)
    acct = Account(institution="WF", name="Checking", type="checking", role="income_bills")
    session.add(acct); session.commit(); session.refresh(acct)
    today = date.today()
    session.add(Transaction(account_id=acct.id, dedupe_key="a", date=today, amount=1000,
                            name="Zelle payment to Example Person 12345"))
    session.add(Transaction(account_id=acct.id, dedupe_key="b", date=today, amount=120, name="TARGET T-1234"))
    session.commit()
    from app.services import categorize
    categorize.apply_all(session)
    with TestClient(app) as c:
        b = c.get("/api/budget").json()
        assert b["totals"]["payroll"] == 2 and b["totals"]["surplus"] == 1998
        prog = c.get("/api/progress").json()
    by = {i["category"]: i for i in prog["items"]}
    assert by["Rent/Housing"]["spent"] == 1000 and by["Rent/Housing"]["status"] == "paid"
    assert by["Gym (payroll)"]["status"] == "payroll"
    assert by["Shopping"]["status"] == "unplanned" and by["Shopping"]["spent"] == 120
    free = prog["free"]
    assert free["committed"] == 1002 and free["unplanned_spent"] == 120 and free["left"] == 3000 - 1002 - 120
