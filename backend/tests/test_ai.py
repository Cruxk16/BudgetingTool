import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app import baseline
from app.db import Account, Transaction, engine
from app.main import app
from app.services import ai


@pytest.fixture()
def client(tmp_path, monkeypatch):
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ai, "KEY_FILE", tmp_path / "gemini.key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with Session(engine) as s:
        f = tmp_path / "b.json"
        f.write_text(json.dumps({"income": 3000, "lines": [
            {"category": "Rent/Housing", "kind": "fixed", "items": [{"name": "Rent", "amount": 1000}]}]}))
        baseline.load(s, f)
        a = Account(institution="WF", name="Checking", type="checking", role="income_bills")
        s.add(a); s.commit(); s.refresh(a)
        s.add(Transaction(account_id=a.id, dedupe_key="1", date=date.today(), amount=42.5, name="TARGET T-1",
                          category="Shopping"))
        s.commit()
    with TestClient(app) as c:
        yield c


def test_requires_key(client):
    r = client.post("/api/ai/ask", json={"question": "hi"})
    assert r.status_code == 400 and "API key" in r.json()["detail"]
    assert client.put("/api/ai/key", json={"key": "abc"}).json()["configured"]
    assert not client.put("/api/ai/key", json={"key": None}).json()["configured"]


def test_tool_loop(client, monkeypatch):
    client.put("/api/ai/key", json={"key": "test"})
    sent = []

    def fake(payload, model):
        sent.append(payload)
        if len(sent) == 1:
            return {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "search_transactions", "args": {"text": "target"}}}]}}]}
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": "You spent $42.50 at Target."}]}}]}

    monkeypatch.setattr(ai, "call_gemini", fake)
    r = client.post("/api/ai/ask", json={"question": "How much at Target?", "history": []}).json()
    assert r["answer"] == "You spent $42.50 at Target." and r["tools_used"] == ["search_transactions"]
    assert r["model"] == ai.DEFAULT_MODEL
    result = sent[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["result"]
    assert result["matches"] == 1 and result["sum_of_all_matches"] == 42.5
    assert "description" in result["transactions"][0] and "account_id" not in result["transactions"][0]


def test_bad_tool_args_dont_crash(client):
    with Session(engine) as s:
        assert "error" in ai.Tools(s).run("search_transactions", {"start_date": "not-a-date"})
        assert "error" in ai.Tools(s).run("drop_tables", {})


def test_model_selection(client, monkeypatch):
    m = client.get("/api/ai/models").json()  # no key -> built-in list
    assert m["current"] == ai.DEFAULT_MODEL and not m["from_google"] and "gemini-3.6-flash" in m["models"]
    assert client.put("/api/ai/model", json={"model": "gemini-3.6-flash"}).json()["model"] == "gemini-3.6-flash"
    client.put("/api/ai/key", json={"key": "test"})
    seen = []
    monkeypatch.setattr(ai, "call_gemini", lambda payload, model: seen.append(model) or
                        {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]})
    client.post("/api/ai/ask", json={"question": "hi"})
    assert seen == ["gemini-3.6-flash"]
    assert client.get("/api/ai/models").json()["current"] == "gemini-3.6-flash"
