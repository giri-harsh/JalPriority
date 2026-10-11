"""M0: health, auth envelope, request ids, 422 re-wrap, logging. OWNER: LEAD."""
from __future__ import annotations

import json
import re
import time

import jwt
import pytest

from jp_a.domain.entities import ErrorEnvelope, HealthResponse

REQ_ID = re.compile(r"^req_[0-9a-f]{12}$")


def assert_envelope(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    ErrorEnvelope.model_validate(body)  # strict
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}
    assert body["error"]["code"] == code
    assert REQ_ID.match(body["error"]["request_id"])
    assert resp.headers["x-request-id"] == body["error"]["request_id"]
    return body["error"]


def test_health_ok_without_token(client):
    r = client.get("/a/v1/health")
    assert r.status_code == 200
    h = HealthResponse.model_validate(r.json())
    assert (h.status, h.service, h.version, h.model_version) == ("ok", "jp-a", "1.0.0", "xgb-sa-v1")
    assert h.time.endswith("Z")
    assert set(r.json()) == {"status", "service", "version", "model_version", "time"}


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/a/v1/scenarios"),
        ("get", "/a/v1/scenarios/scn_sa01_w06"),
        ("post", "/a/v1/scenarios/scn_sa01_w06/disruptions"),
        ("get", "/a/v1/scenarios/scn_sa01_w06/predictions"),
        ("post", "/a/v1/plans/solve"),
        ("get", "/a/v1/model"),
    ],
)
def test_every_route_but_health_requires_token(client, method, path):
    r = getattr(client, method)(path)
    assert_envelope(r, 401, "UNAUTHORIZED")


def test_garbage_token_is_401(client):
    r = client.get("/a/v1/scenarios", headers={"Authorization": "Bearer not-a-jwt"})
    assert_envelope(r, 401, "UNAUTHORIZED")


def test_non_bearer_scheme_is_401(client):
    r = client.get("/a/v1/scenarios", headers={"Authorization": "Basic abc"})
    assert_envelope(r, 401, "UNAUTHORIZED")


def test_wrong_issuer_and_audience_are_401(client, make_token):
    for bad in ({"iss": "someone-else"}, {"aud": "other-audience"}):
        r = client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {make_token(**bad)}"})
        assert_envelope(r, 401, "UNAUTHORIZED")


def test_wrong_secret_is_401(client, make_token):
    tok = make_token(_secret="x" * 48)
    assert_envelope(client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {tok}"}), 401, "UNAUTHORIZED")


def test_expired_token_is_401_but_30s_leeway_applies(client, make_token):
    long_expired = make_token(ttl=-120)
    assert_envelope(client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {long_expired}"}), 401, "UNAUTHORIZED")
    # 10 s past expiry is inside the 30 s leeway -> passes auth (then hits the repo stub / real repo, never 401)
    within_leeway = make_token(ttl=-10)
    r = client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {within_leeway}"})
    assert r.status_code != 401


def test_missing_required_claim_is_401(client, make_token):
    tok = make_token(sub=None)
    assert_envelope(client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {tok}"}), 401, "UNAUTHORIZED")


def test_guest_role_is_403(client, make_token):
    r = client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {make_token(role='guest')}"})
    assert_envelope(r, 403, "FORBIDDEN")


@pytest.mark.parametrize("role", ["dispatcher", "admin", "service"])
def test_allowed_roles_pass_auth(client, make_token, role):
    r = client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {make_token(role=role)}"})
    assert r.status_code not in (401, 403)


def test_alg_none_token_rejected(client):
    tok = jwt.encode({"sub": "x", "role": "admin"}, key="", algorithm="none")
    assert_envelope(client.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {tok}"}), 401, "UNAUTHORIZED")


def test_request_id_generated_and_echoed(client):
    r = client.get("/a/v1/health")
    assert REQ_ID.match(r.headers["x-request-id"])
    r2 = client.get("/a/v1/health", headers={"X-Request-ID": "req_000000000abc"})
    assert r2.headers["x-request-id"] == "req_000000000abc"


def test_request_id_reused_inside_error_envelope(client):
    r = client.get("/a/v1/scenarios", headers={"X-Request-ID": "req_51c0ffee0001"})
    err = assert_envelope(r, 401, "UNAUTHORIZED")
    assert err["request_id"] == "req_51c0ffee0001"


def test_validation_error_is_rewrapped_422(client, auth_headers):
    r = client.post("/a/v1/plans/solve", headers=auth_headers, json={"scenario_id": "scn_sa01_w06"})
    err = assert_envelope(r, 422, "VALIDATION_ERROR")
    errors = err["details"]["errors"]
    assert errors and all(set(e) == {"loc", "msg"} for e in errors)
    assert any(e["loc"][:2] == ["body", "weights"] for e in errors)


def test_validation_error_on_bad_weights(client, auth_headers):
    body = {"scenario_id": "scn_sa01_w06", "weights": {"urgency": 9, "debt": 1, "vulnerability": 0.5, "travel": 20}, "debt_by_area": {}}
    err = assert_envelope(client.post("/a/v1/plans/solve", headers=auth_headers, json=body), 422, "VALIDATION_ERROR")
    assert any("urgency" in e["loc"] for e in err["details"]["errors"])


def test_unknown_route_uses_envelope(client, auth_headers):
    r = client.get("/a/v1/nope", headers=auth_headers)
    assert r.status_code == 404
    assert set(r.json()["error"]) == {"code", "message", "details", "request_id"}


def test_unhandled_exception_becomes_internal_error_envelope(app, auth_headers):
    from fastapi.testclient import TestClient

    class Boom:
        def list_scenarios(self, include_derived=False):
            raise RuntimeError("kaboom")

    app.state.container.scenario_service = Boom()
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/a/v1/scenarios", headers=auth_headers)
    err = assert_envelope(r, 500, "INTERNAL_ERROR")
    assert "kaboom" not in json.dumps(err)  # no internals leaked


def test_auth_disabled_skips_checks(settings):
    from fastapi.testclient import TestClient

    from jp_a.main import create_app

    app = create_app(settings.model_copy(update={"a_auth_disabled": True}))

    class Empty:
        def list_scenarios(self, include_derived=False):
            return []

    app.state.container.scenario_service = Empty()
    with TestClient(app) as c:
        r = c.get("/a/v1/scenarios")
    assert r.status_code == 200 and r.json() == {"scenarios": []}


def test_json_logging_format(capfd, settings):
    from fastapi.testclient import TestClient

    from jp_a.logging_setup import log_event, setup_logging
    from jp_a.main import create_app

    setup_logging("INFO")
    log_event("plan_solved", "solved scn_sa01_w06", duration_ms=412)
    out = capfd.readouterr().out.strip().splitlines()
    rec = json.loads(out[-1])
    assert set(rec) >= {"ts", "level", "service", "request_id", "event", "msg", "duration_ms"}
    assert rec["service"] == "jp-a" and rec["event"] == "plan_solved" and rec["duration_ms"] == 412
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", rec["ts"])
    setup_logging(settings.shared_log_level)


def test_tokens_never_logged(capfd, make_token):
    from fastapi.testclient import TestClient

    from jp_a.config import Settings
    from jp_a.main import create_app

    s = Settings(shared_log_level="INFO")
    app = create_app(s)
    token = make_token()
    with TestClient(app) as c:
        c.get("/a/v1/scenarios", headers={"Authorization": f"Bearer {token}"})
    out = capfd.readouterr().out
    assert token not in out
    assert s.shared_jwt_secret not in out
    from jp_a.logging_setup import setup_logging

    setup_logging("WARNING")
