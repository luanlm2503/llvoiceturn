from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_unknown_route_uses_error_format() -> None:
    res = client.get("/khong-ton-tai")
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "not_found"
    assert set(body) == {"code", "message", "data"}
