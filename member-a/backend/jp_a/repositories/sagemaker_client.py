"""SageMaker endpoint access (M12, stretch). OWNER: QA-INFRA.

``A_MODEL_MODE=sagemaker_endpoint`` with ``A_SAGEMAKER_ENDPOINT=<name>``:

* :class:`SageMakerEndpointClient` invokes the endpoint (``text/csv`` in, one prediction per line out) and can
  resolve the model artefact (``model.tar.gz``) that backs it.
* :class:`SageMakerModelLoader` implements the ``ModelLoader`` protocol on top of that artefact, so the API keeps
  using the same booster for SHAP explanations. ``model_repo`` wraps it in ``FallbackModelLoader``: on ANY failure
  (no credentials, endpoint missing or not ``InService``, bad archive, ...) the local artefacts are used instead.

Nothing here runs at import time and nothing needs network or credentials until an object is built.
"""
from __future__ import annotations

import csv
import io
import logging
import posixpath
import tarfile
from typing import Any
from urllib.parse import urlparse

import numpy as np

from ..logging_setup import log_event
from ..ml.artefacts import ARTEFACT_FILENAME, META_FILENAME
from .storage_s3 import aws_credentials_available, create_boto3_client

MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
_WANTED_MEMBERS = (META_FILENAME, ARTEFACT_FILENAME)


class SageMakerError(RuntimeError):
    """Any failure talking to SageMaker; callers treat it as "use the local artefacts"."""


def _rows_to_csv(matrix: np.ndarray) -> bytes:
    array = np.asarray(matrix, dtype=np.float64)
    if array.ndim != 2 or array.shape[0] == 0:
        raise SageMakerError("expected a non-empty 2-D feature matrix")
    if not np.isfinite(array).all():
        raise SageMakerError("feature matrix holds non-finite values")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in array:
        writer.writerow([repr(float(v)) for v in row])
    return buffer.getvalue().encode("utf-8")


def _parse_prediction_csv(body: bytes, n_rows: int) -> np.ndarray:
    try:
        text = body.decode("utf-8")
        values = [float(token) for token in text.replace("\n", ",").split(",") if token.strip()]
    except (UnicodeDecodeError, ValueError) as exc:
        raise SageMakerError("endpoint returned a non-numeric response") from exc
    result = np.asarray(values, dtype=np.float64)
    if result.shape != (n_rows,) or not np.isfinite(result).all():
        raise SageMakerError(f"endpoint returned {result.size} values for {n_rows} rows")
    return result


class SageMakerEndpointClient:
    def __init__(self, endpoint_name: str, region: str | None = None, runtime_client: Any = None,
                 sagemaker_client: Any = None, s3_client: Any = None):  # fmt: skip
        if not endpoint_name:
            raise ValueError("a SageMaker endpoint name is required")
        self.endpoint_name = endpoint_name
        self.region = region
        self._runtime = runtime_client
        self._sagemaker = sagemaker_client
        self._s3 = s3_client

    # lazily created so constructing the client never needs credentials
    @property
    def runtime(self):
        if self._runtime is None:
            self._runtime = create_boto3_client("sagemaker-runtime", self.region)
        return self._runtime

    @property
    def sagemaker(self):
        if self._sagemaker is None:
            self._sagemaker = create_boto3_client("sagemaker", self.region)
        return self._sagemaker

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = create_boto3_client("s3", self.region)
        return self._s3

    def invoke(self, matrix: np.ndarray) -> np.ndarray:
        """Predicted need (litres) for each feature row. Raises :class:`SageMakerError` on any failure."""
        payload = _rows_to_csv(matrix)
        try:
            response = self.runtime.invoke_endpoint(
                EndpointName=self.endpoint_name, ContentType="text/csv", Accept="text/csv", Body=payload
            )
            body = response["Body"].read()
        except SageMakerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SageMakerError(f"invoke_endpoint failed: {type(exc).__name__}") from exc
        return _parse_prediction_csv(body, int(np.asarray(matrix).shape[0]))

    def model_data_url(self) -> str:
        """``s3://.../model.tar.gz`` of the model behind the endpoint (which must be ``InService``)."""
        try:
            endpoint = self.sagemaker.describe_endpoint(EndpointName=self.endpoint_name)
            status = endpoint.get("EndpointStatus")
            if status != "InService":
                raise SageMakerError(f"endpoint status is {status!r}")
            config = self.sagemaker.describe_endpoint_config(EndpointConfigName=endpoint["EndpointConfigName"])
            model = self.sagemaker.describe_model(ModelName=config["ProductionVariants"][0]["ModelName"])
            container = model.get("PrimaryContainer") or model["Containers"][0]
            url = container["ModelDataUrl"]
        except SageMakerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SageMakerError(f"cannot resolve the endpoint model: {type(exc).__name__}") from exc
        if urlparse(str(url)).scheme != "s3":
            raise SageMakerError("ModelDataUrl is not an s3:// URL")
        return str(url)

    def fetch_artefacts(self) -> dict[str, bytes]:
        """Download the endpoint's ``model.tar.gz`` and return ``{filename: bytes}`` for the two artefacts."""
        url = urlparse(self.model_data_url())
        bucket, key = url.netloc, url.path.lstrip("/")
        try:
            archive = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return extract_artefacts(archive)
        except SageMakerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SageMakerError(f"cannot read the model archive: {type(exc).__name__}") from exc


def extract_artefacts(archive: bytes) -> dict[str, bytes]:
    """Read ``model_meta.json`` and ``shortage_xgb_v1.json`` from a ``.tar.gz`` in memory (no disk writes)."""
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            name = posixpath.basename(member.name.replace("\\", "/"))
            if not member.isfile() or name not in _WANTED_MEMBERS or name in found:
                continue
            if member.size > MAX_ARCHIVE_MEMBER_BYTES:
                raise SageMakerError(f"{name} in the archive is too large")
            handle = tar.extractfile(member)
            if handle is not None:
                found[name] = handle.read()
    missing = [n for n in _WANTED_MEMBERS if n not in found]
    if missing:
        raise SageMakerError(f"archive lacks {', '.join(missing)}")
    return found


class SageMakerModelLoader:
    """``ModelLoader`` whose artefacts are the ones that back the SageMaker endpoint (fetched once, cached)."""

    def __init__(self, client: SageMakerEndpointClient):
        self.client = client
        self.source = f"sagemaker endpoint {client.endpoint_name}"
        self._artefacts: dict[str, bytes] | None = None

    def _get(self) -> dict[str, bytes]:
        if self._artefacts is None:
            self._artefacts = self.client.fetch_artefacts()
        return self._artefacts

    def read_meta(self) -> bytes:
        return self._get()[META_FILENAME]

    def read_booster(self) -> bytearray:
        return bytearray(self._get()[ARTEFACT_FILENAME])


def build_sagemaker_model_loader(settings, client: SageMakerEndpointClient | None = None) -> SageMakerModelLoader | None:
    """Loader for ``A_MODEL_MODE=sagemaker_endpoint`` or ``None`` (with a WARNING) when it cannot be built."""
    region = getattr(settings, "a_aws_region", None)
    endpoint = getattr(settings, "a_sagemaker_endpoint", None)

    def warn(msg: str, reason: str) -> None:
        log_event("model_mode_fallback", msg, level=logging.WARNING, requested_mode="sagemaker_endpoint", reason=reason)

    if not endpoint:
        warn("A_MODEL_MODE=sagemaker_endpoint needs A_SAGEMAKER_ENDPOINT; using local model artefacts", "endpoint_missing")
        return None
    if client is None and not aws_credentials_available(region):
        warn("A_MODEL_MODE=sagemaker_endpoint found no AWS credentials; using local model artefacts", "credentials_missing")
        return None
    try:
        return SageMakerModelLoader(client or SageMakerEndpointClient(endpoint, region))
    except Exception as exc:  # noqa: BLE001
        warn(f"SageMaker client unavailable ({type(exc).__name__}); using local model artefacts", "client_error")
        return None
