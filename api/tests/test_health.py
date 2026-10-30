from fastapi.testclient import TestClient

from main import app


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}
