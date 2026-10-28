from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def test_local_host_allowed():
    assert c.get("/api/accounts", headers={"host": "127.0.0.1:8765"}).status_code == 200
    assert c.get("/api/accounts", headers={"host": "localhost:8765"}).status_code == 200


def test_rebound_host_refused():
    assert c.get("/api/accounts", headers={"host": "evil.example"}).status_code == 403


def test_foreign_origin_write_refused():
    assert c.post("/api/budget/generate", headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/budget/generate", headers={"origin": "http://127.0.0.1:8765"}).status_code != 403
