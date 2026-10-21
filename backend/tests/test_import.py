import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from app.db import engine
from app.main import app
from app.services.importer import ImportFailed, parse_csv

CHASE = """Transaction Date,Post Date,Description,Category,Type,Amount,Memo
03/02/2026,03/03/2026,WHOLE FOODS MARKET,Groceries,Sale,-84.10,
03/05/2026,03/06/2026,NETFLIX.COM,Entertainment,Sale,-15.49,
03/10/2026,03/10/2026,Payment Thank You - Web,,Payment,500.00,
03/12/2026,03/12/2026,AMAZON.COM,Shopping,Return,25.00,
"""
CAPONE = """Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2026-03-02,2026-03-03,1234,SHELL OIL 5744,Gas/Automobile,41.20,
2026-03-09,2026-03-10,1234,CAPITAL ONE MOBILE PYMT,Payment/Credit,,300.00
"""
WELLS = '''"03/01/2026","-1500.00","*","","EXAMPLE PROPERTY RENT"
"03/03/2026","2400.00","*","","ACME CORP PAYROLL"
"03/04/2026","-60.00","*","","VERIZON WIRELESS"
'''


def test_chase_signs():
    rows = parse_csv(CHASE, account_type="credit")
    amt = {r["name"]: r["amount"] for r in rows}
    assert amt["WHOLE FOODS MARKET"] == 84.10      # purchase -> positive
    assert amt["Payment Thank You - Web"] == -500  # payment -> inflow
    assert amt["AMAZON.COM"] == -25                # refund reduces spend
    assert rows[0]["source_category"] == "Groceries"


def test_capital_one_debit_credit():
    amt = {r["name"]: r["amount"] for r in parse_csv(CAPONE)}
    assert amt["SHELL OIL 5744"] == 41.20 and amt["CAPITAL ONE MOBILE PYMT"] == -300


def test_wells_fargo_headerless():
    amt = {r["name"]: r["amount"] for r in parse_csv(WELLS)}
    assert amt["EXAMPLE PROPERTY RENT"] == 1500 and amt["ACME CORP PAYROLL"] == -2400


def test_bad_file():
    with pytest.raises(ImportFailed):
        parse_csv("foo,bar\n1,2\n")


@pytest.fixture()
def client():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def _upload(client, acct_id, name, content):
    """Preview then commit, like the UI does."""
    r = client.post(f"/api/accounts/{acct_id}/import/preview", files={"file": (name, content)})
    if r.status_code != 200:
        return r
    return client.post(f"/api/accounts/{acct_id}/import", json={"rows": r.json()["rows"]})


def test_upload_dedupes_and_categorizes(client):
    acct = client.post("/api/accounts", json={"institution": "Chase", "name": "Freedom", "type": "credit"}).json()
    assert acct["role"] == "spending"
    assert _upload(client, acct["id"], "chase.csv", CHASE).json()["added"] == 4
    again = _upload(client, acct["id"], "chase.csv", CHASE).json()
    assert again["added"] == 0 and again["skipped_duplicates"] == 4

    by = {t["name"]: t for t in client.get("/api/transactions").json()}
    assert by["WHOLE FOODS MARKET"]["category"] == "Groceries"
    assert by["NETFLIX.COM"]["category"] == "Subscriptions"
    assert by["Payment Thank You - Web"]["is_transfer"] and by["Payment Thank You - Web"]["category"] == "Card Payment"
    assert _upload(client, acct["id"], "notes.docx", b"x").status_code == 400


def test_identical_same_day_charges_kept(client):
    acct = client.post("/api/accounts", json={"institution": "X", "name": "Card", "type": "credit"}).json()
    csv = "Date,Description,Amount\n03/01/2026,COFFEE,-4.00\n03/01/2026,COFFEE,-4.00\n"
    assert _upload(client, acct["id"], "a.csv", csv).json()["added"] == 2


def test_capital_one_payment_is_transfer_without_checking_account(client):
    acct = client.post("/api/accounts", json={"institution": "Capital One", "name": "Quicksilver", "type": "credit"}).json()
    csv = CAPONE + "2026-03-15,2026-03-16,1234,SOME ODD PAYMENT LABEL,Payment/Credit,,50.00\n"
    _upload(client, acct["id"], "c1.csv", csv)
    by = {t["name"]: t for t in client.get("/api/transactions").json()}
    assert by["CAPITAL ONE MOBILE PYMT"]["is_transfer"] and by["SOME ODD PAYMENT LABEL"]["is_transfer"]
    assert by["SHELL OIL 5744"]["category"] == "Gas" and not by["SHELL OIL 5744"]["is_transfer"]
