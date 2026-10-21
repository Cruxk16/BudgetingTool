import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.db import Account, PlaidItem, Transaction, engine
from app.main import app
from app.services import plaid


def txn(tid, amount, name, account="chk", day="2026-03-05", pending=False, category="GENERAL_MERCHANDISE_OTHER"):
    return {"transaction_id": tid, "account_id": account, "date": day, "amount": amount, "name": name,
            "merchant_name": None, "pending": pending, "personal_finance_category": {"detailed": category}}


class FakePlaid:
    """Stands in for Plaid's API: records calls and serves queued /transactions/sync pages."""

    def __init__(self):
        self.calls, self.pages = [], []

    def __call__(self, path, body):
        self.calls.append((path, body))
        if path == "/link/token/create":
            return {"link_token": "link-sandbox-123"}
        if path == "/item/public_token/exchange":
            return {"access_token": "access-sandbox-abc", "item_id": "item-1"}
        if path == "/accounts/get":
            return {"accounts": [
                {"account_id": "chk", "name": "Checking", "mask": "0000", "type": "depository", "subtype": "checking"},
                {"account_id": "card", "name": "Card", "mask": "1111", "type": "credit", "subtype": "credit card"},
                {"account_id": "loan", "name": "Student loan", "type": "loan", "subtype": "student"}]}
        if path == "/transactions/sync":
            page = self.pages.pop(0) if self.pages else {}
            if isinstance(page, Exception):
                raise page
            return {"added": [], "modified": [], "removed": [], "next_cursor": "c-end", "has_more": False, **page}
        if path == "/item/remove":
            return {}
        raise AssertionError(path)


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(plaid, "CREDS_FILE", tmp_path / "plaid.json")
    for k in ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV"):
        monkeypatch.delenv(k, raising=False)
    f = FakePlaid()
    monkeypatch.setattr(plaid, "call", f)
    return f


@pytest.fixture()
def client(fake):
    with TestClient(app) as c:
        yield c


def test_credentials_saved_privately(client):
    assert client.get("/api/plaid/status").json()["configured"] is False
    st = client.put("/api/plaid/credentials", json={"client_id": "cid", "secret": "s", "env": "sandbox"}).json()
    assert st["configured"] and st["env"] == "sandbox"
    assert plaid.CREDS_FILE.stat().st_mode & 0o777 == 0o600
    assert client.put("/api/plaid/credentials", json={"client_id": "cid", "secret": "s", "env": "prod"}).status_code == 400
    assert client.put("/api/plaid/credentials", json={}).json()["configured"] is False


def test_link_creates_accounts_and_imports(client, fake):
    fake.pages = [{"added": [txn("t1", 84.10, "WHOLE FOODS", category="FOOD_AND_DRINK_GROCERIES"),
                             txn("t2", -2400, "ACME CORP PAYROLL", category="INCOME_WAGES"),
                             txn("t3", 12.00, "COFFEE", pending=True),
                             txn("t4", -500, "PAYMENT THANK YOU", account="card",
                                 category="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")],
                   "next_cursor": "c1", "has_more": True},
                  {"added": [txn("t5", 30, "SHELL OIL", account="card", category="TRANSPORTATION_GAS")]}]
    r = client.post("/api/plaid/exchange", json={"public_token": "public-sandbox-x", "institution": "Example Bank"}).json()
    assert r["accounts_created"] == 2 and r["added"] == 4  # loan skipped, pending skipped

    with Session(engine) as s:
        accts = {a.plaid_account_id: a for a in s.exec(select(Account))}
        assert set(accts) == {"chk", "card"}
        assert accts["chk"].role == "income_bills" and accts["card"].type == "credit" and accts["card"].mask == "1111"
        by = {t.name: t for t in s.exec(select(Transaction))}
        assert by["WHOLE FOODS"].amount == 84.10 and by["WHOLE FOODS"].category == "Groceries"
        assert by["ACME CORP PAYROLL"].category == "Income"
        assert by["PAYMENT THANK YOU"].is_transfer and by["PAYMENT THANK YOU"].category == "Card Payment"
        assert by["SHELL OIL"].category == "Gas"
        assert s.exec(select(PlaidItem)).one().cursor == "c-end"
    sync_calls = [b for p, b in fake.calls if p == "/transactions/sync"]
    assert "cursor" not in sync_calls[0] and sync_calls[1]["cursor"] == "c1"


def test_sync_applies_changes_and_keeps_manual_edits(client, fake):
    fake.pages = [{"added": [txn("t1", 40, "TARGET"), txn("t2", 15, "NETFLIX.COM")]}]
    client.post("/api/plaid/exchange", json={"public_token": "p", "institution": "Example Bank"})
    with Session(engine) as s:
        tid = s.exec(select(Transaction).where(Transaction.name == "TARGET")).one().id
    client.patch(f"/api/transactions/{tid}", json={"category": "Gifts"})

    fake.pages = [{"modified": [txn("t1", 45, "TARGET")], "removed": [{"transaction_id": "t2"}],
                   "added": [txn("t6", 9, "SPOTIFY USA")]}]
    r = client.post("/api/plaid/sync").json()
    assert (r["added"], r["updated"], r["removed"], r["errors"]) == (1, 1, 1, [])
    with Session(engine) as s:
        by = {t.name: t for t in s.exec(select(Transaction))}
        assert set(by) == {"TARGET", "SPOTIFY USA"}
        assert by["TARGET"].amount == 45 and by["TARGET"].category == "Gifts"
    assert [b.get("cursor") for p, b in fake.calls if p == "/transactions/sync"][-1] == "c-end"


def test_restarts_when_data_changes_mid_sync(client, fake):
    client.post("/api/plaid/exchange", json={"public_token": "p"})
    fake.pages = [{"added": [txn("t1", 5, "A")], "has_more": True, "next_cursor": "c2"},
                  plaid.PlaidError("changed", 400, "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"),
                  {"added": [txn("t1", 5, "A")]}]
    assert client.post("/api/plaid/sync").json()["added"] == 1


def test_broken_login_is_reported_and_reconnectable(client, fake):
    client.post("/api/plaid/exchange", json={"public_token": "p", "institution": "Example Bank"})
    fake.pages = [plaid.PlaidError("login required", 400, "ITEM_LOGIN_REQUIRED")]
    r = client.post("/api/plaid/sync").json()
    assert r["errors"][0]["code"] == "ITEM_LOGIN_REQUIRED"
    item = client.get("/api/plaid/status").json()["items"][0]
    assert item["error"] == "ITEM_LOGIN_REQUIRED"

    client.post("/api/plaid/link_token", json={"item_id": item["id"]})
    path, body = fake.calls[-1]
    assert path == "/link/token/create" and body["access_token"] == "access-sandbox-abc" and "products" not in body
    client.post("/api/plaid/sync")
    assert client.get("/api/plaid/status").json()["items"][0]["error"] is None


def test_disconnect_keeps_history(client, fake):
    fake.pages = [{"added": [txn("t1", 40, "TARGET")]}]
    client.post("/api/plaid/exchange", json={"public_token": "p"})
    item_id = client.get("/api/plaid/status").json()["items"][0]["id"]
    assert client.delete(f"/api/plaid/items/{item_id}").json()["items"] == []
    assert ("/item/remove", {"access_token": "access-sandbox-abc"}) in fake.calls
    with Session(engine) as s:
        assert all(a.plaid_item_id is None for a in s.exec(select(Account)))
        assert len(s.exec(select(Transaction)).all()) == 1
