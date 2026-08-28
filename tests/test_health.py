from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_content_type_json():
    r = client.get("/health")
    assert r.headers["content-type"].startswith("application/json")


def test_root_returns_name_and_version():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "pipelinefixrl-app"
    assert isinstance(body["version"], str) and body["version"]


def test_unknown_route_is_404():
    r = client.get("/nope")
    assert r.status_code == 404
