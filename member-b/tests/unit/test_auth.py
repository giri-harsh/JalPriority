import pytest
from fastapi.testclient import TestClient
from jp_b.main import app
from jp_b.services.auth_service import auth_service

client = TestClient(app)

def test_login_success():
    response = client.post("/b/v1/auth/login", json={
        "username": "officer_01",
        "password": "jal-demo-2026"
    })
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["officer_id"] == "officer_01"
    assert data["role"] == "dispatcher"
    assert "expires_at" in data

def test_login_failure():
    response = client.post("/b/v1/auth/login", json={
        "username": "officer_01",
        "password": "wrongpassword"
    })
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"

def test_get_me_success():
    # Login first
    login_resp = client.post("/b/v1/auth/login", json={
        "username": "officer_01",
        "password": "jal-demo-2026"
    })
    token = login_resp.json()["token"]
    
    # Get me
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    data = me_resp.json()
    assert data["officer_id"] == "officer_01"
    assert data["role"] == "dispatcher"
    assert "expires_at" in data

def test_get_me_unauthorized():
    me_resp = client.get("/b/v1/auth/me")
    assert me_resp.status_code == 401

def test_service_token():
    token = auth_service.get_service_token()
    assert token is not None
    
    # Test caching
    token2 = auth_service.get_service_token()
    assert token == token2
    
    payload = auth_service.verify_token(token)
    assert payload["sub"] == "svc-b"
import time
import jwt
from jp_b.config import settings

def test_token_structure_and_expiry():
    resp = client.post("/b/v1/auth/login", json={
        "username": "officer_01",
        "password": "jal-demo-2026"
    })
    token = resp.json()["token"]
    decoded = jwt.decode(token, settings.shared_jwt_secret, algorithms=["HS256"], audience=settings.shared_jwt_audience, issuer=settings.shared_jwt_issuer)
    
    assert decoded["sub"] == "officer_01"
    assert decoded["role"] == "dispatcher"
    assert decoded["iss"] == settings.shared_jwt_issuer
    assert decoded["aud"] == settings.shared_jwt_audience
    assert "iat" in decoded
    # Expiry should be 8 hours
    assert decoded["exp"] == decoded["iat"] + 8 * 3600

def test_service_token_expiry_and_cache():
    now = int(time.time())
    token1 = auth_service.get_service_token()
    decoded1 = jwt.decode(token1, settings.shared_jwt_secret, algorithms=["HS256"], audience=settings.shared_jwt_audience, issuer=settings.shared_jwt_issuer)
    
    assert decoded1["sub"] == "svc-b"
    assert decoded1["role"] == "service"
    # Expiry should be 5 mins
    assert decoded1["exp"] == decoded1["iat"] + 300
    
    # Check cache (should return same token since we're within 4 mins)
    token2 = auth_service.get_service_token()
    assert token1 == token2

def test_verify_token_invalid_signature():
    resp = client.post("/b/v1/auth/login", json={"username": "officer_01", "password": "jal-demo-2026"})
    token = resp.json()["token"] + "bad"
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 401
    assert me_resp.json()["error"]["code"] == "UNAUTHORIZED"

def test_verify_token_wrong_issuer():
    payload = {
        "iss": "wrong_issuer",
        "aud": settings.shared_jwt_audience,
        "sub": "officer_01",
        "role": "dispatcher",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 401

def test_verify_token_wrong_audience():
    payload = {
        "iss": settings.shared_jwt_issuer,
        "aud": "wrong_audience",
        "sub": "officer_01",
        "role": "dispatcher",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 401

def test_verify_token_expired():
    payload = {
        "iss": settings.shared_jwt_issuer,
        "aud": settings.shared_jwt_audience,
        "sub": "officer_01",
        "role": "dispatcher",
        "iat": int(time.time()) - 3600,
        "exp": int(time.time()) - 60  # Expired 60 seconds ago (beyond 30s leeway)
    }
    token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 401

def test_verify_token_leeway():
    payload = {
        "iss": settings.shared_jwt_issuer,
        "aud": settings.shared_jwt_audience,
        "sub": "officer_01",
        "role": "dispatcher",
        "iat": int(time.time()) - 3600,
        "exp": int(time.time()) - 15  # Expired 15s ago, but leeway is 30s
    }
    token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200

def test_invalid_role():
    payload = {
        "iss": settings.shared_jwt_issuer,
        "aud": settings.shared_jwt_audience,
        "sub": "some_user",
        "role": "invalid_role",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 403
    assert me_resp.json()["error"]["message"] == "Insufficient permissions"

def test_admin_role_allowed():
    payload = {
        "iss": settings.shared_jwt_issuer,
        "aud": settings.shared_jwt_audience,
        "sub": "admin_01",
        "role": "admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    token = jwt.encode(payload, settings.shared_jwt_secret, algorithm="HS256")
    me_resp = client.get("/b/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200

def test_unknown_route_protected():
    resp = client.get("/b/v1/unknown_route")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"

def test_unknown_route_authenticated_returns_404():
    login_resp = client.post("/b/v1/auth/login", json={
        "username": "officer_01",
        "password": "jal-demo-2026"
    })
    token = login_resp.json()["token"]
    resp = client.get("/b/v1/unknown_route", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert "X-Request-ID" in resp.headers

def test_validation_error_envelope():
    # Pass invalid payload to login (missing password)
    resp = client.post("/b/v1/auth/login", json={"username": "officer_01"})
    assert resp.status_code == 422
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "details" in data["error"]
    assert "request_id" in data["error"]
    assert resp.headers.get("X-Request-ID") == data["error"]["request_id"]

