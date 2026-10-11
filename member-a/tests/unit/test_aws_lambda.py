"""M12: Mangum Lambda entry point (jp_a.lambda_handler) driven with API Gateway HTTP API v2 events. No network."""
from __future__ import annotations

import json
import os

import pytest

from jp_a import lambda_handler as lh
from jp_a.domain.entities import HealthResponse


class FakeContext:
    function_name = "jp-a-api"
    memory_limit_in_mb = 2048
    invoked_function_arn = "arn:aws:lambda:ap-south-1:123456789012:function:jp-a-api"
    aws_request_id = "00000000-0000-0000-0000-000000000000"

    @staticmethod
    def get_remaining_time_in_millis() -> int:
        return 15000


def http_api_event(method: str, path: str, headers: dict[str, str] | None = None, body: str | None = None) -> dict:
    return {
        "version": "2.0",
        "routeKey": "ANY /{proxy+}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "abc123.execute-api.ap-south-1.amazonaws.com", **(headers or {})},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc123",
            "domainName": "abc123.execute-api.ap-south-1.amazonaws.com",
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.9", "userAgent": "pytest"},
            "requestId": "req-1",
            "routeKey": "ANY /{proxy+}",
            "stage": "$default",
            "time": "01/Jan/2026:00:00:00 +0000",
            "timeEpoch": 1767225600000,
        },
        "body": body,
        "isBase64Encoded": False,
    }


def test_the_handler_is_a_mangum_adapter_over_the_real_app():
    from jp_a.main import app

    assert callable(lh.handler)
    assert type(lh.handler).__name__ == "Mangum"
    assert lh.handler.app is app


def test_health_through_the_lambda_handler():
    response = lh.handler(http_api_event("GET", "/a/v1/health"), FakeContext())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    HealthResponse.model_validate(body)
    assert body["status"] == "ok" and body["model_version"] == "xgb-sa-v1"
    headers = {k.lower(): v for k, v in response["headers"].items()}
    assert headers["x-request-id"]


def test_protected_routes_still_require_a_token_behind_the_handler():
    response = lh.handler(http_api_event("GET", "/a/v1/scenarios"), FakeContext())
    assert response["statusCode"] == 401
    assert json.loads(response["body"])["error"]["code"] == "UNAUTHORIZED"


def test_authenticated_call_through_the_handler(make_token):
    event = http_api_event("GET", "/a/v1/scenarios", {"authorization": f"Bearer {make_token()}"})
    response = lh.handler(event, FakeContext())
    assert response["statusCode"] == 200
    ids = [row["scenario_id"] for row in json.loads(response["body"])["scenarios"]]
    assert "scn_sa01_w06" in ids


def test_unknown_paths_use_the_error_envelope():
    response = lh.handler(http_api_event("GET", "/a/v1/nope"), FakeContext())
    assert response["statusCode"] == 404
    assert json.loads(response["body"])["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------------------ writable data directory

def test_read_only_data_dir_is_copied_to_a_writable_location(tmp_path, monkeypatch):
    source = tmp_path / "task" / "data"
    (source / "scenarios").mkdir(parents=True)
    (source / "scenarios" / "s.json").write_text("{}", encoding="utf-8")
    env = {"A_DATA_DIR": str(source)}
    monkeypatch.setattr(lh.os, "access", lambda path, mode: False)  # a read-only image filesystem
    copy = tmp_path / "tmp-a-data"

    assert lh.ensure_writable_data_dir(env, copy_to=str(copy)) == str(copy)
    assert env["A_DATA_DIR"] == str(copy)
    assert (copy / "scenarios" / "s.json").read_text(encoding="utf-8") == "{}"
    # a warm start reuses the copy instead of copying again
    (copy / "scenarios" / "extra.json").write_text("{}", encoding="utf-8")
    env["A_DATA_DIR"] = str(source)
    assert lh.ensure_writable_data_dir(env, copy_to=str(copy)) == str(copy)
    assert (copy / "scenarios" / "extra.json").exists()


def test_writable_missing_or_unset_data_dirs_are_left_alone(tmp_path):
    writable = tmp_path / "data"
    writable.mkdir()
    env = {"A_DATA_DIR": str(writable)}
    assert lh.ensure_writable_data_dir(env, copy_to=str(tmp_path / "copy")) is None
    assert env["A_DATA_DIR"] == str(writable) and not (tmp_path / "copy").exists()

    env = {"A_DATA_DIR": str(tmp_path / "missing")}
    assert lh.ensure_writable_data_dir(env, copy_to=str(tmp_path / "copy")) is None
    assert lh.ensure_writable_data_dir({}, copy_to=str(tmp_path / "copy")) is None


def test_copy_failure_keeps_the_original_setting(tmp_path, monkeypatch):
    source = tmp_path / "data"
    source.mkdir()
    env = {"A_DATA_DIR": str(source)}
    monkeypatch.setattr(lh.os, "access", lambda path, mode: False)

    def broken_copy(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(lh.shutil, "copytree", broken_copy)
    assert lh.ensure_writable_data_dir(env, copy_to=str(tmp_path / "copy")) is None
    assert env["A_DATA_DIR"] == str(source)


def test_the_default_copy_target_is_inside_tmp():
    assert lh.LAMBDA_DATA_COPY.startswith("/tmp/")


@pytest.mark.skipif(os.name == "nt", reason="directory write bits are not meaningful on Windows")
def test_real_read_only_directory_is_detected(tmp_path):
    source = tmp_path / "ro"
    source.mkdir()
    (source / "f.json").write_text("{}", encoding="utf-8")
    source.chmod(0o555)
    try:
        if os.access(source, os.W_OK):  # running as root: permissions are not enforced
            pytest.skip("directory is writable for this user")
        env = {"A_DATA_DIR": str(source)}
        assert lh.ensure_writable_data_dir(env, copy_to=str(tmp_path / "copy")) == str(tmp_path / "copy")
    finally:
        source.chmod(0o755)
