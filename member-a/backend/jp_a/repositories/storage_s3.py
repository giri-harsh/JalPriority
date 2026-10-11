"""S3 storage backend and S3 model loader (M12, stretch). OWNER: QA-INFRA.

``A_STORAGE_MODE=s3`` -> :class:`S3Storage` (same ``StorageBackend`` interface as ``LocalStorage``):
keys are ``<A_S3_PREFIX><relpath>``, e.g. ``a/scenarios/scn_sa01_w06.json``, ``a/derived/<id>.json``.

``A_MODEL_MODE=s3`` -> :class:`S3ModelLoader` (the ``ModelLoader`` protocol of ``model_repo``): reads
``<prefix>models/model_meta.json`` and ``<prefix>models/shortage_xgb_v1.json``.

Everything here is optional. ``make_s3_storage`` / ``build_s3_model_loader`` return ``None`` (after a WARNING
log) when the bucket, credentials or boto3 are missing, so callers fall back to the local implementation.
Nothing in this module touches the network at import time.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from ..logging_setup import log_event
from ..ml.artefacts import ARTEFACT_FILENAME, META_FILENAME

MODELS_SUBPREFIX = "models"
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


def normalise_prefix(prefix: str | None) -> str:
    """``"a"`` / ``"/a/"`` / ``"a/"`` -> ``"a/"``; ``None`` / ``""`` -> ``""``."""
    cleaned = str(prefix or "").replace("\\", "/").strip("/")
    return f"{cleaned}/" if cleaned else ""


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return ""


def is_missing_key_error(exc: BaseException) -> bool:
    return _error_code(exc) in _MISSING_CODES


def create_boto3_client(service: str, region: str | None):
    """boto3 client with short timeouts and one retry (a Lambda has 15 s in total)."""
    import boto3
    from botocore.config import Config

    config = Config(
        region_name=region,
        connect_timeout=3,
        read_timeout=5,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    return boto3.client(service, region_name=region, config=config)


def aws_credentials_available(region: str | None = None) -> bool:
    """True when the default boto3 credential chain resolves to something. Never raises."""
    try:
        import boto3

        return boto3.session.Session(region_name=region).get_credentials() is not None
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return False


def _fallback_warning(event: str, msg: str, **fields: Any) -> None:
    log_event(event, msg, level=logging.WARNING, **fields)


def _validated_relpath(relpath: str) -> tuple[str, ...]:
    rel = PurePosixPath(str(relpath).replace("\\", "/"))
    if not rel.parts or rel.is_absolute() or any(p in ("..", "") for p in rel.parts) or ":" in str(rel):
        raise ValueError(f"invalid storage path: {relpath!r}")
    return rel.parts


class S3Storage:
    """JSON documents in one S3 bucket under ``prefix``; the same interface and path rules as ``LocalStorage``.

    ``seed_storage`` (optional, read-only use) is a second backend consulted for documents that are not in the
    bucket yet - typically the ``LocalStorage`` that ships inside the container image with the seed scenarios,
    so a fresh, unseeded bucket still serves the three seeds. Writes always go to S3.
    """

    def __init__(self, bucket: str, prefix: str = "a/", client: Any = None, region: str | None = None,
                 seed_storage: Any = None):  # fmt: skip
        if not bucket:
            raise ValueError("an S3 bucket name is required")
        self.bucket = bucket
        self.prefix = normalise_prefix(prefix)
        self.region = region
        self.seed_storage = seed_storage
        self.client = client if client is not None else create_boto3_client("s3", region)

    # -- helpers -------------------------------------------------------------------------------
    def _key(self, relpath: str) -> str:
        return self.prefix + "/".join(_validated_relpath(relpath))

    def verify(self) -> None:
        """Raise when the bucket is not reachable with the current credentials (a cheap HEAD)."""
        self.client.head_bucket(Bucket=self.bucket)

    def _get_bytes(self, key: str) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 - botocore raises ClientError subclasses
            if is_missing_key_error(exc):
                return None
            raise
        return response["Body"].read()

    # -- StorageBackend ------------------------------------------------------------------------
    def read_json(self, relpath: str) -> Any | None:
        key = self._key(relpath)
        raw = self._get_bytes(key)
        if raw is None:
            return self.seed_storage.read_json(relpath) if self.seed_storage is not None else None
        return json.loads(raw.decode("utf-8"))

    def write_json(self, relpath: str, data: Any) -> None:
        from .storage_local import json_text

        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(relpath),
            Body=json_text(data).encode("utf-8"),
            ContentType="application/json",
        )

    def exists(self, relpath: str) -> bool:
        key = self._key(relpath)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:  # noqa: BLE001
            if not is_missing_key_error(exc):
                raise
        return bool(self.seed_storage is not None and self.seed_storage.exists(relpath))

    def list_json(self, prefix: str) -> list[str]:
        """Sorted relpaths of the ``*.json`` objects directly under ``prefix`` (like ``LocalStorage``)."""
        parts = _validated_relpath(prefix)
        base = "/".join(parts)
        key_prefix = f"{self.prefix}{base}/"
        found: set[str] = set()
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": key_prefix, "Delimiter": "/"}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for item in page.get("Contents", []):
                name = item["Key"][len(key_prefix):]
                if name and "/" not in name and name.endswith(".json"):
                    found.add(f"{base}/{name}")
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
            if not token:
                break
        if self.seed_storage is not None:
            found.update(self.seed_storage.list_json(prefix))
        return sorted(found)

    # -- seeding -------------------------------------------------------------------------------
    def upload_json_tree(self, local: Any, prefixes: tuple[str, ...] = ("scenarios", "truth")) -> list[str]:
        """Copy ``prefixes`` (+ ``labels.json``) from a ``LocalStorage``-like backend into the bucket."""
        uploaded: list[str] = []
        for prefix in prefixes:
            for relpath in local.list_json(prefix):
                self.write_json(relpath, local.read_json(relpath))
                uploaded.append(relpath)
        labels = local.read_json("labels.json")
        if labels is not None:
            self.write_json("labels.json", labels)
            uploaded.append("labels.json")
        return uploaded


def make_s3_storage(settings, seed_storage: Any = None, client: Any = None) -> S3Storage | None:
    """Build the S3 backend, or return ``None`` (with a WARNING) when it cannot be used.

    Missing bucket, missing credentials, missing boto3 or an unreachable bucket all mean "use local storage".
    ``client`` is for tests (an injected client skips the credential check).
    """
    bucket = getattr(settings, "a_s3_bucket", None)
    region = getattr(settings, "a_aws_region", None)
    if not bucket:
        _fallback_warning("storage_fallback", "A_STORAGE_MODE=s3 needs A_S3_BUCKET; using local storage",
                          reason="bucket_missing")  # fmt: skip
        return None
    if client is None and not aws_credentials_available(region):
        _fallback_warning("storage_fallback", "A_STORAGE_MODE=s3 found no AWS credentials; using local storage",
                          reason="credentials_missing", bucket=bucket)  # fmt: skip
        return None
    try:
        storage = S3Storage(bucket, getattr(settings, "a_s3_prefix", "a/"), client=client, region=region,
                            seed_storage=seed_storage)  # fmt: skip
        storage.verify()
    except Exception as exc:  # noqa: BLE001 - ImportError, ClientError, EndpointConnectionError, ...
        _fallback_warning("storage_fallback", f"S3 storage unavailable ({type(exc).__name__}); using local storage",
                          reason="s3_error", bucket=bucket, error=type(exc).__name__)  # fmt: skip
        return None
    log_event("storage_s3_ready", f"using s3://{bucket}/{storage.prefix}", bucket=bucket, prefix=storage.prefix)
    return storage


class S3ModelLoader:
    """``ModelLoader`` that reads the model artefacts from ``s3://<bucket>/<prefix>models/``."""

    def __init__(self, bucket: str, prefix: str = "a/", client: Any = None, region: str | None = None):
        if not bucket:
            raise ValueError("an S3 bucket name is required")
        self.bucket = bucket
        self.prefix = normalise_prefix(prefix)
        self.client = client if client is not None else create_boto3_client("s3", region)
        self.source = f"s3://{bucket}/{self.prefix}{MODELS_SUBPREFIX}/"

    def _fetch(self, filename: str) -> bytes:
        key = f"{self.prefix}{MODELS_SUBPREFIX}/{filename}"
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001
            if is_missing_key_error(exc):
                raise FileNotFoundError(2, "S3 object not found", key) from exc
            raise OSError(f"S3 read failed for {key}: {type(exc).__name__}") from exc

    def read_meta(self) -> bytes:
        return self._fetch(META_FILENAME)

    def read_booster(self) -> bytearray:
        return bytearray(self._fetch(ARTEFACT_FILENAME))


class FallbackModelLoader:
    """Try a remote ``primary`` loader once; on ANY failure serve the ``fallback`` (local) artefacts instead.

    Meta and booster always come from the same source: both are fetched together on first use and cached, so a
    half-uploaded bucket can never mix an S3 meta with a local booster. ``validate(meta, booster)`` (optional)
    may raise to reject a corrupt remote artefact, which also triggers the fallback.
    """

    def __init__(self, primary: Any, fallback: Any, requested_mode: str,
                 validate: Callable[[bytes, bytearray], None] | None = None):  # fmt: skip
        self.primary = primary
        self.fallback = fallback
        self.requested_mode = requested_mode
        self.validate = validate
        self.active_mode = requested_mode
        self.source = primary.source
        self._pair: tuple[bytes, bytearray] | None = None

    def _resolve(self) -> tuple[bytes, bytearray]:
        if self._pair is not None:
            return self._pair
        try:
            meta = self.primary.read_meta()
            booster = self.primary.read_booster()
            if self.validate is not None:
                self.validate(meta, booster)
            self._pair = (meta, booster)
        except Exception as exc:  # noqa: BLE001 - any remote failure means "use local"
            _fallback_warning(
                "model_mode_fallback",
                f"A_MODEL_MODE={self.requested_mode} failed ({type(exc).__name__}); using local model artefacts",
                requested_mode=self.requested_mode,
                reason="remote_failed",
                error=type(exc).__name__,
            )
            self.active_mode = "local"
            self.source = self.fallback.source
            # A missing local artefact is left to raise so the repository answers 503 (not cached: retried later).
            return self.fallback.read_meta(), self.fallback.read_booster()
        return self._pair

    def read_meta(self) -> bytes:
        return self._resolve()[0]

    def read_booster(self) -> bytearray:
        return bytearray(self._resolve()[1])


def build_s3_model_loader(settings, client: Any = None) -> S3ModelLoader | None:
    """``S3ModelLoader`` for ``A_MODEL_MODE=s3`` or ``None`` (with a WARNING) when it cannot be built."""
    bucket = getattr(settings, "a_s3_bucket", None)
    region = getattr(settings, "a_aws_region", None)
    if not bucket:
        _fallback_warning("model_mode_fallback", "A_MODEL_MODE=s3 needs A_S3_BUCKET; using local model artefacts",
                          requested_mode="s3", reason="bucket_missing")  # fmt: skip
        return None
    if client is None and not aws_credentials_available(region):
        _fallback_warning("model_mode_fallback", "A_MODEL_MODE=s3 found no AWS credentials; using local model artefacts",
                          requested_mode="s3", reason="credentials_missing")  # fmt: skip
        return None
    try:
        return S3ModelLoader(bucket, getattr(settings, "a_s3_prefix", "a/"), client=client, region=region)
    except Exception as exc:  # noqa: BLE001
        _fallback_warning("model_mode_fallback", f"S3 model loader unavailable ({type(exc).__name__}); using local",
                          requested_mode="s3", reason="s3_error", error=type(exc).__name__)  # fmt: skip
        return None
