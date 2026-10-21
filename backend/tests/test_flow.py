from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from app.db import engine
from app.main import app
from app.services import goals as goals_svc


@pytest.fixture()
def client():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def test_demo_flow(client):
    assert client.post("/api/demo/seed").json()["transactions"] > 300

    txns = client.get("/api/transactions?limit=1000").json()
    payments = [t for t in txns if any(w in t["name"] for w in ("PYMT", "AUTOPAY", "PAYMENT", "CREDIT RECEIVED"))]
    assert payments and all(t["is_transfer"] for t in payments)  # incl. unlabeled Capital One pairs

    inc = client.get("/api/income").json()
    assert inc["sources"][0]["frequency"] == "biweekly"
    assert inc["monthly"] == pytest.approx(2400 * 26 / 12, abs=1)

    b = client.post("/api/budget/generate").json()
    by = {l["category"]: l for l in b["lines"]}
    assert by["Rent/Housing"]["kind"] == "fixed" and by["Rent/Housing"]["amount"] == 1500
    assert by["Car Payment"]["amount"] == 350
    assert by["Groceries"]["kind"] == "variable"
    assert any(l["kind"] == "sinking" for l in b["lines"])
    assert b["totals"]["surplus"] > 0
    assert client.get("/api/progress").json()["items"]
    assert client.get("/api/insights").json()["subscriptions"]["items"]


def test_goal_math():
    assert goals_svc.months_to_goal(1000, 300) == 4
    assert goals_svc.months_to_goal(0, 300) == 0
    assert goals_svc.months_to_goal(1000, 0) is None


def test_goals_api(client):
    client.post("/api/demo/seed")
    client.post("/api/budget/generate")
    r = client.post("/api/goals", json={"name": "Laptop", "price": 2000, "priority": 1}).json()
    g = r["goals"][0]
    assert g["months_to_goal"] and g["faster"]
    r = client.post(f"/api/goals/{g['id']}/contributions", json={"amount": 500}).json()
    assert r["goals"][0]["saved"] == 500
