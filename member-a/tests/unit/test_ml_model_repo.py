"""M3: ModelRepository - loading, idempotence, thread safety, 503 paths, SHAP predictions."""
from __future__ import annotations

import json
import shutil
import threading

import numpy as np
import pytest

from jp_a.api.errors import ModelUnavailableError
from jp_a.domain.entities import FEATURE_NAMES, ModelInfo, Scenario
from jp_a.logging_setup import setup_logging
from jp_a.ml.artefacts import ARTEFACT_FILENAME, META_FILENAME
from jp_a.ml.features import FeatureError, build_feature_frame
from jp_a.repositories.model_repo import LocalModelLoader, ModelRepository

from .ml_helpers import load_scenario_fixture, train_synthetic_model


@pytest.fixture(scope="module")
def trained_dir(tmp_path_factory):
    models_dir, _ = train_synthetic_model(tmp_path_factory.mktemp("ml_repo"))
    return models_dir


@pytest.fixture()
def models_copy(trained_dir, tmp_path):
    target = tmp_path / "models"
    shutil.copytree(trained_dir, target)
    return target


def make_repo(settings, models_dir, **overrides) -> ModelRepository:
    return ModelRepository(settings.model_copy(update={"a_models_dir": models_dir, **overrides}))


@pytest.fixture()
def features():
    return build_feature_frame(Scenario.model_validate(load_scenario_fixture()))


def test_load_and_model_info(settings, trained_dir):
    repo = make_repo(settings, trained_dir)
    repo.load()
    assert repo.is_available()
    info = repo.get_model_info()
    assert isinstance(info, ModelInfo)
    ModelInfo.model_validate(info.model_dump(mode="json"))  # strict round trip
    meta = json.loads((trained_dir / META_FILENAME).read_text(encoding="utf-8"))
    assert info.model_dump(mode="json") == {k: meta[k] for k in ModelInfo.model_fields}
    assert repo.model_version == "xgb-sa-v1"
    assert repo.active_mode == "local"


def test_get_model_info_loads_lazily(settings, trained_dir):
    repo = make_repo(settings, trained_dir)
    assert repo.get_model_info().train_rows > 0


def test_load_is_idempotent(settings, trained_dir):
    repo = make_repo(settings, trained_dir)
    repo.load()
    first = repo._loaded
    repo.load()
    assert repo._loaded is first


class CountingLoader(LocalModelLoader):
    def __init__(self, models_dir):
        super().__init__(models_dir)
        self.meta_reads = 0
        self.lock = threading.Lock()

    def read_meta(self) -> bytes:
        with self.lock:
            self.meta_reads += 1
        return super().read_meta()


def test_concurrent_loads_read_artefacts_once(settings, trained_dir):
    loader = CountingLoader(trained_dir)
    repo = ModelRepository(settings, loader=loader)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait()
            repo.load()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert loader.meta_reads == 1


def test_missing_artefact_is_503_and_recovers(settings, trained_dir, tmp_path):
    empty = tmp_path / "models"
    empty.mkdir()
    repo = make_repo(settings, empty)
    assert repo.is_available() is False
    with pytest.raises(ModelUnavailableError) as exc_info:
        repo.get_model_info()
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "MODEL_UNAVAILABLE"
    assert exc_info.value.details["reason"] == "artefact_missing"
    with pytest.raises(ModelUnavailableError):
        repo.load()
    assert repo.model_version == "xgb-sa-v1"  # health keeps working without a model
    # Once the artefacts appear, the next call loads them (no permanent failure state).
    for name in (ARTEFACT_FILENAME, META_FILENAME):
        shutil.copy(trained_dir / name, empty / name)
    assert repo.is_available() is True


def test_missing_meta_only_is_503(settings, models_copy):
    (models_copy / META_FILENAME).unlink()
    with pytest.raises(ModelUnavailableError):
        make_repo(settings, models_copy).load()


def test_missing_models_dir_is_503(settings, tmp_path):
    with pytest.raises(ModelUnavailableError):
        make_repo(settings, tmp_path / "does-not-exist").load()


@pytest.mark.parametrize(
    "content,reason",
    [
        ("{not json", "meta_invalid"),
        ("[1, 2, 3]", "meta_invalid"),
    ],
)
def test_corrupt_meta_is_503(settings, models_copy, content, reason):
    (models_copy / META_FILENAME).write_text(content, encoding="utf-8")
    with pytest.raises(ModelUnavailableError) as exc_info:
        make_repo(settings, models_copy).load()
    assert exc_info.value.details["reason"] == reason


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda m: m.pop("metrics"), "meta_invalid"),
        (lambda m: m.update(is_simulation=False), "meta_invalid"),
        (lambda m: m.update(trained_at="yesterday"), "meta_invalid"),
        (lambda m: m["feature_importances"].append({"feature": "rainfall_mm", "importance": 0.0}), "meta_invalid"),
        (lambda m: m.update(feature_names=list(reversed(FEATURE_NAMES))), "meta_feature_mismatch"),
        (lambda m: m.pop("feature_names"), "meta_feature_mismatch"),
    ],
)
def test_invalid_meta_fields_are_503(settings, models_copy, mutate, reason):
    path = models_copy / META_FILENAME
    meta = json.loads(path.read_text(encoding="utf-8"))
    mutate(meta)
    path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ModelUnavailableError) as exc_info:
        make_repo(settings, models_copy).load()
    assert exc_info.value.details["reason"] == reason


def test_corrupt_booster_is_503(settings, models_copy):
    (models_copy / ARTEFACT_FILENAME).write_text('{"learner": "garbage"}', encoding="utf-8")
    repo = make_repo(settings, models_copy)
    with pytest.raises(ModelUnavailableError) as exc_info:
        repo.load()
    assert exc_info.value.details["reason"] == "artefact_corrupt"
    assert repo.is_available() is False


def test_predict_shapes_and_shap_additivity(settings, trained_dir, features):
    repo = make_repo(settings, trained_dir)
    preds, contribs = repo.predict(features)
    assert preds.shape == (3,)
    assert contribs.shape == (3, len(FEATURE_NAMES) + 1)
    assert preds.dtype == np.float64 and contribs.dtype == np.float64
    # SHAP contributions (incl. the bias, last column) add up to the prediction.
    np.testing.assert_allclose(contribs.sum(axis=1), preds, rtol=1e-4)
    # The bias column is constant across rows (expected value of the model).
    assert np.allclose(contribs[:, -1], contribs[0, -1])


def test_predict_is_column_order_insensitive(settings, trained_dir, features):
    repo = make_repo(settings, trained_dir)
    preds, contribs = repo.predict(features)
    preds2, contribs2 = repo.predict(features[list(reversed(FEATURE_NAMES))])
    np.testing.assert_array_equal(preds, preds2)
    np.testing.assert_array_equal(contribs, contribs2)


def test_predict_rejects_missing_columns(settings, trained_dir, features):
    with pytest.raises(FeatureError):
        make_repo(settings, trained_dir).predict(features.drop(columns=["heat_index_c"]))


def test_predict_without_model_is_503(settings, tmp_path, features):
    with pytest.raises(ModelUnavailableError):
        make_repo(settings, tmp_path).predict(features)


def test_predict_is_thread_safe(settings, trained_dir, features):
    repo = make_repo(settings, trained_dir)
    expected = repo.predict(features)[0]
    results: list[np.ndarray] = []

    def worker():
        for _ in range(20):
            results.append(repo.predict(features)[0])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 80
    assert all(np.array_equal(r, expected) for r in results)


@pytest.mark.parametrize("mode", ["s3", "sagemaker_endpoint"])
def test_non_local_model_mode_falls_back_to_local(settings, trained_dir, mode, capfd):
    setup_logging("WARNING")
    try:
        repo = make_repo(settings, trained_dir, a_model_mode=mode)
        out = capfd.readouterr().out
    finally:
        setup_logging(settings.shared_log_level)
    record = json.loads(next(line for line in out.splitlines() if "model_mode_fallback" in line))
    assert record["level"] == "WARNING" and record["requested_mode"] == mode
    assert repo.active_mode == "local"
    assert repo.is_available()
