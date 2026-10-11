"""M12: S3Storage + make_storage fallbacks (fake client / botocore Stubber; no network, no credentials)."""
# ruff: noqa: F811
from __future__ import annotations

import json
import logging

import boto3
import pytest
from botocore.stub import Stubber

from jp_a.config import Settings
from jp_a.domain.entities import Scenario
from jp_a.repositories import storage_s3
from jp_a.repositories.scenario_repo import ScenarioRepository
from jp_a.repositories.storage_local import LocalStorage, json_text, make_storage
from jp_a.repositories.storage_s3 import S3Storage, make_s3_storage, normalise_prefix

from .ml_helpers import SEED_SCENARIO_IDS, copy_real_data, derived_scenario_dict
from .test_aws_fakes import FakeS3Client, captured_logs, client_error, no_aws_env  # noqa: F401

BUCKET = "jalpriority-a-test"


@pytest.fixture()
def fake() -> FakeS3Client:
    return FakeS3Client(BUCKET)


@pytest.fixture()
def s3(fake) -> S3Storage:
    return S3Storage(BUCKET, "a/", client=fake)


def s3_settings(tmp_path, **overrides) -> Settings:
    base = {"a_data_dir": tmp_path, "a_storage_mode": "s3", "a_s3_bucket": BUCKET, "shared_log_level": "WARNING"}
    return Settings(**{**base, **overrides})


# ---------------------------------------------------------------------------------------- the interface

def test_json_round_trip_uses_the_prefixed_key_and_canonical_text(s3, fake):
    data = {"scenario_id": "scn_x", "areas": [{"id": "ar_01", "name": "Kothrud \u2013 Pune"}], "n": 1.5}
    s3.write_json("scenarios/scn_x.json", data)
    assert list(fake.objects) == ["a/scenarios/scn_x.json"]
    assert fake.objects["a/scenarios/scn_x.json"].decode("utf-8") == json_text(data)
    assert fake.last_put_kwargs["ContentType"] == "application/json"
    assert s3.read_json("scenarios/scn_x.json") == data


def test_matches_local_storage_byte_for_byte(s3, fake, tmp_path):
    data = {"b": [1, 2, {"c": None}], "a": "x"}
    local = LocalStorage(tmp_path)
    local.write_json("derived/d.json", data)
    s3.write_json("derived/d.json", data)
    assert fake.objects["a/derived/d.json"] == (tmp_path / "derived" / "d.json").read_bytes()


def test_read_missing_is_none_and_exists_reflects_objects(s3):
    assert s3.read_json("scenarios/nope.json") is None
    assert s3.exists("scenarios/nope.json") is False
    s3.write_json("scenarios/yes.json", {})
    assert s3.exists("scenarios/yes.json") is True


def test_list_json_is_sorted_direct_children_only_and_paginates(fake):
    fake.page_size = 2
    store = S3Storage(BUCKET, "a/", client=fake)
    for name in ("c", "a", "b", "d"):
        store.write_json(f"derived/{name}.json", {})
    fake.objects["a/derived/readme.txt"] = b"x"
    fake.objects["a/derived/nested/deep.json"] = b"{}"
    fake.objects["a/scenarios/other.json"] = b"{}"
    assert store.list_json("derived") == [f"derived/{n}.json" for n in "abcd"]
    assert store.list_json("empty") == []


def test_prefix_normalisation(fake):
    assert normalise_prefix("a") == normalise_prefix("/a/") == normalise_prefix("a/") == "a/"
    assert normalise_prefix("") == normalise_prefix(None) == ""
    bare = S3Storage(BUCKET, "", client=fake)
    bare.write_json("labels.json", {"k": 1})
    assert "labels.json" in fake.objects


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs.json", "a/../../b.json", "c:/x.json", ""])
def test_path_traversal_is_rejected_like_local_storage(s3, bad):
    for call in (s3.read_json, s3.exists):
        with pytest.raises(ValueError):
            call(bad)
    with pytest.raises(ValueError):
        s3.write_json(bad, {})


def test_unexpected_errors_are_not_mistaken_for_missing(s3, fake):
    fake.fail_with["get_object"] = client_error("AccessDenied", "GetObject", 403)
    with pytest.raises(Exception, match="AccessDenied"):
        s3.read_json("scenarios/x.json")
    fake.fail_with["head_object"] = client_error("403", "HeadObject", 403)
    with pytest.raises(Exception, match="403"):
        s3.exists("scenarios/x.json")


def test_bucket_name_is_required(fake):
    with pytest.raises(ValueError):
        S3Storage("", "a/", client=fake)


# ---------------------------------------------------------------------------------- real client + Stubber

def stubbed_client():
    client = boto3.client("s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test")
    return client, Stubber(client)


def test_stubber_missing_key_means_none_and_put_has_expected_parameters():
    client, stub = stubbed_client()
    body = json_text({"ok": True}).encode("utf-8")
    stub.add_client_error("get_object", "NoSuchKey", expected_params={"Bucket": BUCKET, "Key": "a/scenarios/m.json"})
    stub.add_response(
        "put_object", {}, {"Bucket": BUCKET, "Key": "a/derived/d.json", "Body": body, "ContentType": "application/json"}
    )
    stub.add_client_error("head_object", "404", 404, expected_params={"Bucket": BUCKET, "Key": "a/derived/z.json"})
    with stub:
        store = S3Storage(BUCKET, "a/", client=client)
        assert store.read_json("scenarios/m.json") is None
        store.write_json("derived/d.json", {"ok": True})
        assert store.exists("derived/z.json") is False
        stub.assert_no_pending_responses()


def test_stubber_list_uses_delimiter_and_continuation():
    client, stub = stubbed_client()
    base = {"Bucket": BUCKET, "Prefix": "a/scenarios/", "Delimiter": "/"}
    stub.add_response(
        "list_objects_v2",
        {"IsTruncated": True, "NextContinuationToken": "t1", "Contents": [{"Key": "a/scenarios/b.json"}]},
        base,
    )
    stub.add_response(
        "list_objects_v2",
        {"IsTruncated": False, "Contents": [{"Key": "a/scenarios/a.json"}, {"Key": "a/scenarios/"}]},
        {**base, "ContinuationToken": "t1"},
    )
    with stub:
        assert S3Storage(BUCKET, "a/", client=client).list_json("scenarios") == ["scenarios/a.json", "scenarios/b.json"]
        stub.assert_no_pending_responses()


# ------------------------------------------------------------------------------------------- seed storage

def test_unseeded_bucket_serves_the_local_seeds_and_writes_to_s3(fake, tmp_path):
    data_dir = copy_real_data(tmp_path / "data")
    local = LocalStorage(data_dir)
    store = S3Storage(BUCKET, "a/", client=fake, seed_storage=local)
    repo = ScenarioRepository(Settings(a_data_dir=data_dir), store)

    assert [s.scenario_id for s in repo.list_summaries()] == sorted(SEED_SCENARIO_IDS)
    assert store.exists("scenarios/scn_sa01_w06.json")
    assert repo.get("scn_sa01_w06").scenario_id == "scn_sa01_w06"
    assert store.read_json("truth/scn_sa01_w06.json") == local.read_json("truth/scn_sa01_w06.json")

    derived = Scenario.model_validate(derived_scenario_dict())
    repo.save_derived(derived)
    assert f"a/derived/{derived.scenario_id}.json" in fake.objects  # persisted in S3 ...
    assert not (data_dir / "derived" / f"{derived.scenario_id}.json").exists()  # ... not on local disk
    assert repo.get(derived.scenario_id).scenario_id == derived.scenario_id


def test_s3_copy_wins_over_the_seed(fake, tmp_path):
    local = LocalStorage(tmp_path)
    local.write_json("scenarios/s.json", {"from": "local"})
    store = S3Storage(BUCKET, "a/", client=fake, seed_storage=local)
    assert store.read_json("scenarios/s.json") == {"from": "local"}
    store.write_json("scenarios/s.json", {"from": "s3"})
    assert store.read_json("scenarios/s.json") == {"from": "s3"}
    assert store.list_json("scenarios") == ["scenarios/s.json"]


def test_upload_json_tree_seeds_the_bucket(fake, tmp_path):
    local = LocalStorage(copy_real_data(tmp_path / "data"))
    store = S3Storage(BUCKET, "a/", client=fake)
    uploaded = store.upload_json_tree(local)
    assert "labels.json" in uploaded and f"scenarios/{SEED_SCENARIO_IDS[0]}.json" in uploaded
    assert sorted(fake.objects) == sorted(f"a/{p}" for p in uploaded)
    assert store.read_json("labels.json") == local.read_json("labels.json")


# ---------------------------------------------------------------------------------------- make_storage

def test_make_storage_s3_builds_an_s3_backend_when_everything_is_in_place(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(storage_s3, "aws_credentials_available", lambda region=None: True)
    monkeypatch.setattr(storage_s3, "create_boto3_client", lambda service, region: fake)
    storage = make_storage(s3_settings(tmp_path, a_s3_prefix="a/"))
    assert isinstance(storage, S3Storage)
    assert storage.bucket == BUCKET and storage.prefix == "a/"
    assert isinstance(storage.seed_storage, LocalStorage) and storage.seed_storage.root == tmp_path


def test_make_storage_local_default_never_touches_aws(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("AWS must not be touched in local mode")

    monkeypatch.setattr(storage_s3, "create_boto3_client", boom)
    monkeypatch.setattr(storage_s3, "aws_credentials_available", boom)
    storage = make_storage(Settings(a_data_dir=tmp_path))
    assert isinstance(storage, LocalStorage)
    assert Settings().a_storage_mode == "local"


def test_fallback_without_a_bucket(tmp_path, captured_logs):
    storage = make_storage(s3_settings(tmp_path, a_s3_bucket=None))
    assert isinstance(storage, LocalStorage) and storage.root == tmp_path
    warnings = [r for r in captured_logs.records if getattr(r, "event", "") == "storage_fallback"]
    assert warnings and warnings[0].levelno == logging.WARNING and warnings[0].reason == "bucket_missing"


def test_fallback_without_credentials(tmp_path, no_aws_env, captured_logs):  # noqa: F811
    storage = make_storage(s3_settings(tmp_path))
    assert isinstance(storage, LocalStorage)
    warnings = [r for r in captured_logs.records if getattr(r, "event", "") == "storage_fallback"]
    assert warnings and warnings[0].reason == "credentials_missing"


@pytest.mark.parametrize(
    "error",
    [client_error("403", "HeadBucket", 403), client_error("404", "HeadBucket", 404), ConnectionError("no route")],
)
def test_fallback_when_the_bucket_is_unreachable(tmp_path, monkeypatch, fake, captured_logs, error):
    fake.fail_with["head_bucket"] = error
    monkeypatch.setattr(storage_s3, "aws_credentials_available", lambda region=None: True)
    monkeypatch.setattr(storage_s3, "create_boto3_client", lambda service, region: fake)
    storage = make_storage(s3_settings(tmp_path))
    assert isinstance(storage, LocalStorage)
    assert "storage_fallback" in captured_logs.events(logging.WARNING)


def test_fallback_when_boto3_errors_at_init(tmp_path, monkeypatch, captured_logs):
    def broken(service, region):
        raise ImportError("boto3 is not installed")

    monkeypatch.setattr(storage_s3, "aws_credentials_available", lambda region=None: True)
    monkeypatch.setattr(storage_s3, "create_boto3_client", broken)
    assert isinstance(make_storage(s3_settings(tmp_path)), LocalStorage)
    assert "storage_fallback" in captured_logs.events(logging.WARNING)


def test_fallback_when_the_s3_module_itself_blows_up(tmp_path, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(storage_s3, "make_s3_storage", broken)
    assert isinstance(make_storage(s3_settings(tmp_path)), LocalStorage)


def test_make_s3_storage_returns_none_instead_of_raising(tmp_path):
    assert make_s3_storage(s3_settings(tmp_path, a_s3_bucket=None)) is None


def test_credentials_probe_never_raises(monkeypatch, no_aws_env):  # noqa: F811
    assert storage_s3.aws_credentials_available("ap-south-1") is False
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    assert storage_s3.aws_credentials_available("ap-south-1") is True


def test_stored_documents_are_valid_json_text(s3, fake):
    s3.write_json("derived/x.json", {"é": "ü"})
    raw = fake.objects["a/derived/x.json"]
    assert raw.endswith(b"\n") and b"\r" not in raw
    assert json.loads(raw) == {"é": "ü"}
