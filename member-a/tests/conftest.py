"""Shared fixtures. OWNER: LEAD. Agents add test-local conftest.py files inside their own test folders.

    settings / app / client      real app, auth ENABLED, built from Settings() overrides
    make_token(**claims)         mint an HS256 token with CONTRACT section 8 claims (override any claim)
    auth_headers                 {"Authorization": "Bearer <dispatcher token>"}
    load_fixture(name)           parsed JSON from tests/fixtures/<name>.json  (name with or without .json)
    fixtures_dir / repo_root / member_a_dir
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from jp_a.config import Settings
from jp_a.main import create_app

TESTS_DIR = Path(__file__).resolve().parent
MEMBER_A_DIR = TESTS_DIR.parent
REPO_ROOT = MEMBER_A_DIR.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"

DEV_SECRET = "dev-only-jalpriority-secret-change-me-0123456789"


def _mint(settings: Settings, *, ttl: int = 3600, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.shared_jwt_issuer,
        "aud": settings.shared_jwt_audience,
        "sub": "officer_01",
        "role": "dispatcher",
        "iat": now,
        "exp": now + ttl,
    }
    secret = overrides.pop("_secret", settings.shared_jwt_secret)
    for k, v in overrides.items():
        if v is None:
            claims.pop(k, None)
        else:
            claims[k] = v
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.fixture(scope="session")
def member_a_dir() -> Path:
    return MEMBER_A_DIR


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def load_fixture():
    def _load(name: str):
        path = FIXTURES_DIR / (name if name.endswith(".json") else f"{name}.json")
        return json.loads(path.read_text(encoding="utf-8"))

    return _load


@pytest.fixture(scope="session")
def settings() -> Settings:
    # Explicit values so a developer's local .env cannot change test behaviour.
    return Settings(
        a_auth_disabled=False,
        shared_jwt_secret=DEV_SECRET,
        shared_jwt_issuer="jalpriority-b",
        shared_jwt_audience="jalpriority",
        shared_env="local",
        shared_log_level="WARNING",
    )


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def make_token(settings):
    def _make(**kwargs) -> str:
        return _mint(settings, **kwargs)

    return _make


@pytest.fixture()
def auth_headers(make_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}
