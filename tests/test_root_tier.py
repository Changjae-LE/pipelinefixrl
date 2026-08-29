from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_includes_tier():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "tier" in body
    assert isinstance(body["tier"], str) and body["tier"]
