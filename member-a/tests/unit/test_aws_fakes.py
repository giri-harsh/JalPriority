"""Shared fakes for the M12 AWS tests (no tests in this module). No network, no credentials."""
from __future__ import annotations

import io
import logging
import tarfile
from typing import Any

import pytest
from botocore.exceptions import ClientError


def client_error(code: str, operation: str = "Op", status: int = 400) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation)


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """In-memory S3 with the handful of calls the code uses (paginated listing included)."""

    def __init__(self, bucket: str = "jalpriority-a-test", page_size: int = 1000):
        self.bucket = bucket
        self.page_size = page_size
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_with: dict[str, Exception] = {}  # operation name -> exception to raise

    def _maybe_fail(self, op: str) -> None:
        if op in self.fail_with:
            raise self.fail_with[op]

    def head_bucket(self, Bucket: str):  # noqa: N803 - boto3 naming
        self._maybe_fail("head_bucket")
        if Bucket != self.bucket:
            raise client_error("404", "HeadBucket", 404)
        return {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: Any):  # noqa: N803
        self._maybe_fail("put_object")
        self.calls.append(("put", Key))
        self.objects[Key] = bytes(Body)
        self.last_put_kwargs = kwargs
        return {}

    def get_object(self, Bucket: str, Key: str):  # noqa: N803
        self._maybe_fail("get_object")
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise client_error("NoSuchKey", "GetObject", 404)
        return {"Body": _Body(self.objects[Key])}

    def head_object(self, Bucket: str, Key: str):  # noqa: N803
        self._maybe_fail("head_object")
        if Key not in self.objects:
            raise client_error("404", "HeadObject", 404)
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, Bucket: str, Prefix: str = "", Delimiter: str | None = None,  # noqa: N803
                        ContinuationToken: str | None = None):  # fmt: skip
        self._maybe_fail("list_objects_v2")
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if Delimiter:
            keys = [k for k in keys if Delimiter not in k[len(Prefix):]]
        start = int(ContinuationToken or 0)
        chunk = keys[start : start + self.page_size]
        truncated = start + self.page_size < len(keys)
        page: dict[str, Any] = {"Contents": [{"Key": k} for k in chunk], "IsTruncated": truncated}
        if truncated:
            page["NextContinuationToken"] = str(start + self.page_size)
        return page


def make_model_targz(files: dict[str, bytes], nested_dir: str = "") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{nested_dir}{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def events(self, level: int | None = None) -> list[str]:
        return [getattr(r, "event", "") for r in self.records if level is None or r.levelno >= level]


@pytest.fixture()
def no_aws_env(monkeypatch):
    """No ambient AWS credentials, config files or instance-metadata lookups can leak into a test."""
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
                 "AWS_DEFAULT_PROFILE", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                 "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_WEB_IDENTITY_TOKEN_FILE"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent/credentials")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent/config")


@pytest.fixture()
def captured_logs():
    logger = logging.getLogger("jp_a")
    handler = _ListHandler()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
