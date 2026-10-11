from fastapi.testclient import TestClient
from jp_b.main import app

client = TestClient(app)

def test_health():
    response = client.get("/b/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "jp-b"
    assert data["version"] == "1.0.0"
    assert "time" in data

def test_request_id_middleware():
    response = client.get("/b/v1/health")
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"].startswith("req_")

def test_request_id_echo():
    response = client.get("/b/v1/health", headers={"X-Request-ID": "req_1234567890ab"})
    assert response.headers["X-Request-ID"] == "req_1234567890ab"
