"""M12: A_MODEL_MODE=s3 / sagemaker_endpoint - loaders, fallbacks, ModelRepository selection (no network)."""
# ruff: noqa: F811
from __future__ import annotations

import json
import logging

import boto3
import numpy as np
import pytest
from botocore.stub import Stubber

from jp_a.api.errors import ModelUnavailableError
from jp_a.config import Settings
from jp_a.domain.entities import Scenario
from jp_a.ml.artefacts import ARTEFACT_FILENAME, META_FILENAME
from jp_a.ml.features import build_feature_frame
from jp_a.repositories import sagemaker_client, storage_s3
from jp_a.repositories.model_repo import LocalModelLoader, ModelRepository
from jp_a.repositories.sagemaker_client import (
    SageMakerEndpointClient,
    SageMakerError,
    SageMakerModelLoader,
    extract_artefacts,
)
from jp_a.repositories.storage_s3 import FallbackModelLoader, S3ModelLoader

from .ml_helpers import REAL_MODELS_DIR, load_scenario_fixture
from .test_aws_fakes import FakeS3Client, captured_logs, client_error, make_model_targz, no_aws_env  # noqa: F401

BUCKET = "jalpriority-a-test"
ENDPOINT = "jp-a-shortage"
MODEL_URL = f"s3://{BUCKET}/a/training/output/model.tar.gz"


@pytest.fixture(scope="module")
def real_meta() -> bytes:
    return (REAL_MODELS_DIR / META_FILENAME).read_bytes()


@pytest.fixture(scope="module")
def real_booster() -> bytes:
    return (REAL_MODELS_DIR / ARTEFACT_FILENAME).read_bytes()


@pytest.fixture(scope="module")
def local_reference():
    repo = ModelRepository(Settings(a_models_dir=REAL_MODELS_DIR, a_model_mode="local"))
    repo.load()
    return repo


@pytest.fixture()
def features():
    return build_feature_frame(Scenario.model_validate(load_scenario_fixture()))


def model_settings(**overrides) -> Settings:
    base = {"a_models_dir": REAL_MODELS_DIR, "a_model_mode": "s3", "a_s3_bucket": BUCKET, "shared_log_level": "WARNING"}
    return Settings(**{**base, **overrides})


@pytest.fixture()
def s3_bucket(monkeypatch, real_meta, real_booster) -> FakeS3Client:
    """A fake bucket holding the real artefacts under a/models/, wired in as the default boto3 client."""
    fake = FakeS3Client(BUCKET)
    fake.objects[f"a/models/{META_FILENAME}"] = real_meta
    fake.objects[f"a/models/{ARTEFACT_FILENAME}"] = real_booster
    monkeypatch.setattr(storage_s3, "aws_credentials_available", lambda region=None: True)
    monkeypatch.setattr(storage_s3, "create_boto3_client", lambda service, region: fake)
    return fake


# ------------------------------------------------------------------------------------------ S3ModelLoader

def test_s3_model_loader_reads_both_artefacts(s3_bucket, real_meta, real_booster):
    loader = S3ModelLoader(BUCKET, "a/", client=s3_bucket)
    assert loader.read_meta() == real_meta
    assert bytes(loader.read_booster()) == real_booster
    assert loader.source == f"s3://{BUCKET}/a/models/"


def test_s3_model_loader_missing_object_is_file_not_found(s3_bucket):
    del s3_bucket.objects[f"a/models/{ARTEFACT_FILENAME}"]
    loader = S3ModelLoader(BUCKET, "a/", client=s3_bucket)
    with pytest.raises(FileNotFoundError):
        loader.read_booster()


def test_s3_model_loader_other_errors_are_os_errors(s3_bucket):
    s3_bucket.fail_with["get_object"] = client_error("AccessDenied", "GetObject", 403)
    with pytest.raises(OSError, match="AccessDenied|S3 read failed"):
        S3ModelLoader(BUCKET, "a/", client=s3_bucket).read_meta()


def test_s3_model_loader_stubber_requests_the_models_prefix():
    client = boto3.client("s3", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.add_client_error("get_object", "NoSuchKey", expected_params={"Bucket": BUCKET, "Key": f"a/models/{META_FILENAME}"})
    with stub:
        with pytest.raises(FileNotFoundError):
            S3ModelLoader(BUCKET, "a/", client=client).read_meta()
        stub.assert_no_pending_responses()


# ------------------------------------------------------------------------ ModelRepository: s3 mode selection

def test_s3_mode_serves_the_s3_artefacts(s3_bucket, local_reference, features):
    repo = ModelRepository(model_settings())
    assert repo.active_mode == "s3"
    repo.load()
    assert repo.is_available() and repo.active_mode == "s3"
    assert repo.get_model_info() == local_reference.get_model_info()
    pred, contribs = repo.predict(features)
    ref_pred, ref_contribs = local_reference.predict(features)
    np.testing.assert_allclose(pred, ref_pred)
    np.testing.assert_allclose(contribs, ref_contribs)
    assert ("get", f"a/models/{ARTEFACT_FILENAME}") in s3_bucket.calls


def test_s3_mode_loads_s3_artefacts_even_if_local_ones_are_gone(s3_bucket, tmp_path):
    repo = ModelRepository(model_settings(a_models_dir=tmp_path / "empty"))
    assert repo.is_available() and repo.active_mode == "s3"


def test_s3_mode_missing_objects_fall_back_to_local(s3_bucket, features, captured_logs):
    s3_bucket.objects.clear()
    repo = ModelRepository(model_settings())
    assert repo.active_mode == "s3"  # until a load proves otherwise
    assert repo.is_available()
    assert repo.active_mode == "local"
    assert repo.predict(features)[0].shape == (len(features),)
    assert "model_mode_fallback" in captured_logs.events(logging.WARNING)


def test_s3_mode_half_uploaded_bucket_never_mixes_sources(s3_bucket, local_reference):
    meta = json.loads(s3_bucket.objects[f"a/models/{META_FILENAME}"])
    meta["train_rows"] = 1  # a recognisably different S3 meta ...
    s3_bucket.objects[f"a/models/{META_FILENAME}"] = json.dumps(meta).encode("utf-8")
    del s3_bucket.objects[f"a/models/{ARTEFACT_FILENAME}"]  # ... whose booster is missing
    repo = ModelRepository(model_settings())
    assert repo.get_model_info() == local_reference.get_model_info()  # fully local, not the S3 meta
    assert repo.active_mode == "local"


@pytest.mark.parametrize("corrupt", ["booster", "meta"])
def test_s3_mode_corrupt_artefacts_fall_back_to_local(s3_bucket, local_reference, corrupt):
    key = f"a/models/{ARTEFACT_FILENAME if corrupt == 'booster' else META_FILENAME}"
    s3_bucket.objects[key] = b"this is not a model"
    repo = ModelRepository(model_settings())
    assert repo.get_model_info() == local_reference.get_model_info()
    assert repo.active_mode == "local"


def test_s3_mode_errors_fall_back_to_local(s3_bucket):
    s3_bucket.fail_with["get_object"] = ConnectionError("network down")
    repo = ModelRepository(model_settings())
    assert repo.is_available() and repo.active_mode == "local"


def test_s3_mode_without_bucket_or_credentials_is_plain_local(no_aws_env, captured_logs):  # noqa: F811
    for overrides in ({"a_s3_bucket": None}, {}):
        repo = ModelRepository(model_settings(**overrides))
        assert isinstance(repo._loader, LocalModelLoader)
        assert repo.active_mode == "local" and repo.is_available()
    assert captured_logs.events(logging.WARNING).count("model_mode_fallback") == 2


def test_local_mode_never_touches_aws(monkeypatch, features):
    def boom(*_a, **_k):
        raise AssertionError("AWS must not be touched in local mode")

    monkeypatch.setattr(storage_s3, "create_boto3_client", boom)
    monkeypatch.setattr(storage_s3, "aws_credentials_available", boom)
    monkeypatch.setattr(sagemaker_client, "create_boto3_client", boom)
    repo = ModelRepository(model_settings(a_model_mode="local"))
    assert repo.active_mode == "local" and repo.is_available()


def test_missing_local_artefacts_still_answer_503_after_a_failed_remote(s3_bucket, tmp_path):
    s3_bucket.objects.clear()
    repo = ModelRepository(model_settings(a_models_dir=tmp_path / "nothing"))
    with pytest.raises(ModelUnavailableError):
        repo.get_model_info()
    assert repo.is_available() is False


def test_an_injected_loader_is_used_as_is(real_meta, real_booster, tmp_path):
    class Injected:
        source = "injected"

        def read_meta(self):
            return real_meta

        def read_booster(self):
            return bytearray(real_booster)

    repo = ModelRepository(model_settings(a_models_dir=tmp_path / "nothing"), loader=Injected())
    assert repo.is_available() and repo.active_mode == "local"


# ----------------------------------------------------------------------------------- FallbackModelLoader

class RecordingLoader:
    def __init__(self, meta=b"{}", booster=b"x", error: Exception | None = None, source="primary"):
        self.meta, self.booster, self.error, self.source = meta, booster, error, source
        self.reads = 0

    def read_meta(self):
        self.reads += 1
        if self.error:
            raise self.error
        return self.meta

    def read_booster(self):
        self.reads += 1
        if self.error:
            raise self.error
        return bytearray(self.booster)


def test_fallback_loader_fetches_the_primary_once_and_caches():
    primary, local = RecordingLoader(), RecordingLoader(source="local")
    loader = FallbackModelLoader(primary, local, "s3")
    assert loader.read_meta() == b"{}" and bytes(loader.read_booster()) == b"x"
    assert loader.read_meta() == b"{}"
    assert primary.reads == 2 and local.reads == 0 and loader.active_mode == "s3"


def test_fallback_loader_validator_rejection_triggers_the_fallback(captured_logs):
    def reject(meta, booster):
        raise ValueError("bad artefact")

    primary, local = RecordingLoader(), RecordingLoader(meta=b"L", booster=b"L", source="local-dir")
    loader = FallbackModelLoader(primary, local, "sagemaker_endpoint", validate=reject)
    assert loader.read_meta() == b"L" and bytes(loader.read_booster()) == b"L"
    assert loader.active_mode == "local" and loader.source == "local-dir"
    record = next(r for r in captured_logs.records if getattr(r, "event", "") == "model_mode_fallback")
    assert record.levelno == logging.WARNING and record.requested_mode == "sagemaker_endpoint"


def test_fallback_loader_propagates_local_failure():
    loader = FallbackModelLoader(RecordingLoader(error=OSError("x")), RecordingLoader(error=FileNotFoundError("y")), "s3")
    with pytest.raises(FileNotFoundError):
        loader.read_meta()


# ------------------------------------------------------------------------------- SageMaker: the client

class FakeRuntime:
    def __init__(self, reply: bytes | Exception):
        self.reply, self.requests = reply, []

    def invoke_endpoint(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"Body": type("B", (), {"read": lambda _self, reply=self.reply: reply})()}


def test_invoke_sends_csv_and_parses_predictions():
    runtime = FakeRuntime(b"1200.5\n80.0\n")
    client = SageMakerEndpointClient(ENDPOINT, "ap-south-1", runtime_client=runtime)
    result = client.invoke(np.array([[1.0, 2.5, 3.0], [4.0, 5.0, 6.25]]))
    np.testing.assert_allclose(result, [1200.5, 80.0])
    request = runtime.requests[0]
    assert request["EndpointName"] == ENDPOINT and request["ContentType"] == "text/csv"
    assert request["Body"].decode("utf-8") == "1.0,2.5,3.0\n4.0,5.0,6.25\n"


def test_invoke_accepts_a_single_line_csv_response():
    runtime = FakeRuntime(b"1.0,2.0,3.0")
    client = SageMakerEndpointClient(ENDPOINT, runtime_client=runtime)
    np.testing.assert_allclose(client.invoke(np.zeros((3, 2))), [1.0, 2.0, 3.0])


@pytest.mark.parametrize(
    "reply",
    [b"1.0\n", b"abc\n", b"", b"nan\n1.0\n", b"\xff\xfe", ConnectionError("down"), client_error("ModelError", "InvokeEndpoint")],
)
def test_invoke_failures_are_sagemaker_errors(reply):
    client = SageMakerEndpointClient(ENDPOINT, runtime_client=FakeRuntime(reply))
    with pytest.raises(SageMakerError):
        client.invoke(np.zeros((2, 3)))


@pytest.mark.parametrize("matrix", [np.zeros((0, 3)), np.zeros(3), np.array([[1.0, np.inf]])])
def test_invoke_rejects_bad_input_without_calling_the_endpoint(matrix):
    runtime = FakeRuntime(b"1.0\n")
    with pytest.raises(SageMakerError):
        SageMakerEndpointClient(ENDPOINT, runtime_client=runtime).invoke(matrix)
    assert runtime.requests == []


def sagemaker_stub(status="InService", url=MODEL_URL):
    client = boto3.client("sagemaker", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.add_response(
        "describe_endpoint",
        {"EndpointName": ENDPOINT, "EndpointArn": "arn:aws:sagemaker:ap-south-1:123456789012:endpoint/jp-a-shortage",
         "EndpointConfigName": "jp-a-config", "EndpointStatus": status, "CreationTime": 0, "LastModifiedTime": 0},
        {"EndpointName": ENDPOINT},
    )  # fmt: skip
    if status == "InService":
        stub.add_response(
            "describe_endpoint_config",
            {"EndpointConfigName": "jp-a-config", "EndpointConfigArn": "arn:aws:sagemaker:ap-south-1:123456789012:endpoint-config/jp-a-config",
             "ProductionVariants": [{"VariantName": "AllTraffic", "ModelName": "jp-a-model"}], "CreationTime": 0},
            {"EndpointConfigName": "jp-a-config"},
        )  # fmt: skip
        stub.add_response(
            "describe_model",
            {"ModelName": "jp-a-model", "ExecutionRoleArn": "arn:aws:iam::123456789012:role/r",
             "PrimaryContainer": {"Image": "123456789012.dkr.ecr.ap-south-1.amazonaws.com/x:1", "ModelDataUrl": url},
             "CreationTime": 0, "ModelArn": "arn:aws:sagemaker:ap-south-1:123456789012:model/jp-a-model"},
            {"ModelName": "jp-a-model"},
        )  # fmt: skip
    return client, stub


def test_model_data_url_follows_endpoint_config_model():
    client, stub = sagemaker_stub()
    with stub:
        assert SageMakerEndpointClient(ENDPOINT, sagemaker_client=client).model_data_url() == MODEL_URL
        stub.assert_no_pending_responses()


def test_model_data_url_requires_an_in_service_endpoint():
    client, stub = sagemaker_stub(status="Creating")
    with stub, pytest.raises(SageMakerError, match="Creating"):
        SageMakerEndpointClient(ENDPOINT, sagemaker_client=client).model_data_url()


def test_model_data_url_rejects_non_s3_urls():
    client, stub = sagemaker_stub(url="https://example.com/model.tar.gz")
    with stub, pytest.raises(SageMakerError, match="s3"):
        SageMakerEndpointClient(ENDPOINT, sagemaker_client=client).model_data_url()


def test_model_data_url_wraps_aws_errors():
    client = boto3.client("sagemaker", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.add_client_error("describe_endpoint", "ValidationException", "Could not find endpoint")
    with stub, pytest.raises(SageMakerError):
        SageMakerEndpointClient(ENDPOINT, sagemaker_client=client).model_data_url()


def test_constructing_a_client_needs_no_credentials(no_aws_env):  # noqa: F811
    client = SageMakerEndpointClient(ENDPOINT, "ap-south-1")
    assert client.endpoint_name == ENDPOINT
    with pytest.raises(ValueError):
        SageMakerEndpointClient("", "ap-south-1")


# ---------------------------------------------------------------------------- SageMaker: archive + loader

def test_extract_artefacts_finds_nested_members(real_meta, real_booster):
    archive = make_model_targz({META_FILENAME: real_meta, ARTEFACT_FILENAME: real_booster, "other.txt": b"x"}, "model/")
    found = extract_artefacts(archive)
    assert found == {META_FILENAME: real_meta, ARTEFACT_FILENAME: real_booster}


def test_extract_artefacts_requires_both_files(real_meta):
    with pytest.raises(SageMakerError, match=ARTEFACT_FILENAME):
        extract_artefacts(make_model_targz({META_FILENAME: real_meta}))
    with pytest.raises(Exception):  # noqa: B017 - not a gzip tarball at all
        extract_artefacts(b"garbage")


class SageMakerWorld:
    """Fake boto3 'sagemaker' + 's3' clients for the full endpoint -> archive -> artefacts chain."""

    def __init__(self, archive: bytes, status: str = "InService"):
        self.s3 = FakeS3Client(BUCKET)
        self.s3.objects["a/training/output/model.tar.gz"] = archive
        self.status = status

    def describe_endpoint(self, EndpointName):  # noqa: N803
        return {"EndpointStatus": self.status, "EndpointConfigName": "c"}

    def describe_endpoint_config(self, EndpointConfigName):  # noqa: N803
        return {"ProductionVariants": [{"ModelName": "m"}]}

    def describe_model(self, ModelName):  # noqa: N803
        return {"PrimaryContainer": {"ModelDataUrl": MODEL_URL}}

    def factory(self):
        return lambda service, region: self.s3 if service == "s3" else self


@pytest.fixture()
def sagemaker_world(monkeypatch, real_meta, real_booster):
    world = SageMakerWorld(make_model_targz({META_FILENAME: real_meta, ARTEFACT_FILENAME: real_booster}))
    monkeypatch.setattr(storage_s3, "aws_credentials_available", lambda region=None: True)
    monkeypatch.setattr(sagemaker_client, "aws_credentials_available", lambda region=None: True)
    monkeypatch.setattr(sagemaker_client, "create_boto3_client", world.factory())
    return world


def endpoint_settings(**overrides) -> Settings:
    return model_settings(**{"a_model_mode": "sagemaker_endpoint", "a_sagemaker_endpoint": ENDPOINT, **overrides})


def test_sagemaker_loader_returns_the_endpoint_artefacts(sagemaker_world, real_meta, real_booster):
    loader = SageMakerModelLoader(SageMakerEndpointClient(ENDPOINT, "ap-south-1", sagemaker_client=sagemaker_world,
                                                          s3_client=sagemaker_world.s3))  # fmt: skip
    assert loader.read_meta() == real_meta and bytes(loader.read_booster()) == real_booster
    assert loader.source == f"sagemaker endpoint {ENDPOINT}"
    assert sagemaker_world.s3.calls.count(("get", "a/training/output/model.tar.gz")) == 1  # downloaded once


def test_sagemaker_mode_serves_the_endpoint_model(sagemaker_world, local_reference, features):
    repo = ModelRepository(endpoint_settings())
    repo.load()
    assert repo.active_mode == "sagemaker_endpoint"
    assert repo.get_model_info() == local_reference.get_model_info()
    np.testing.assert_allclose(repo.predict(features)[0], local_reference.predict(features)[0])


@pytest.mark.parametrize("status", ["Creating", "Failed", "OutOfService"])
def test_sagemaker_mode_endpoint_not_in_service_falls_back(sagemaker_world, status, captured_logs):
    sagemaker_world.status = status
    repo = ModelRepository(endpoint_settings())
    assert repo.is_available() and repo.active_mode == "local"
    assert "model_mode_fallback" in captured_logs.events(logging.WARNING)


def test_sagemaker_mode_bad_archive_falls_back(sagemaker_world):
    sagemaker_world.s3.objects["a/training/output/model.tar.gz"] = b"not a tarball"
    repo = ModelRepository(endpoint_settings())
    assert repo.is_available() and repo.active_mode == "local"


def test_sagemaker_mode_corrupt_booster_in_archive_falls_back(sagemaker_world, real_meta):
    sagemaker_world.s3.objects["a/training/output/model.tar.gz"] = make_model_targz(
        {META_FILENAME: real_meta, ARTEFACT_FILENAME: b"corrupt"}
    )
    repo = ModelRepository(endpoint_settings())
    assert repo.is_available() and repo.active_mode == "local"


def test_sagemaker_mode_aws_errors_fall_back(sagemaker_world):
    def explode(**_kwargs):
        raise client_error("ThrottlingException", "DescribeEndpoint")

    sagemaker_world.describe_endpoint = explode
    repo = ModelRepository(endpoint_settings())
    assert repo.is_available() and repo.active_mode == "local"


def test_sagemaker_mode_without_endpoint_or_credentials_is_plain_local(no_aws_env, captured_logs):  # noqa: F811
    for overrides in ({"a_sagemaker_endpoint": None}, {}):
        repo = ModelRepository(endpoint_settings(**overrides))
        assert isinstance(repo._loader, LocalModelLoader)
        assert repo.active_mode == "local" and repo.is_available()
    assert captured_logs.events(logging.WARNING).count("model_mode_fallback") == 2


def test_aws_setup_failures_never_break_the_repository(monkeypatch):
    def broken(_settings):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(storage_s3, "build_s3_model_loader", broken)
    monkeypatch.setattr(sagemaker_client, "build_sagemaker_model_loader", broken)
    for mode in ("s3", "sagemaker_endpoint"):
        repo = ModelRepository(endpoint_settings(a_model_mode=mode))
        assert repo.active_mode == "local" and repo.is_available()
