"""M3: the committed model artefact (models/shortage_xgb_v1.json + model_meta.json) against data/training/*.csv.

These tests pin the honesty of the reported numbers: every metric in model_meta.json must be reproducible from
the committed booster and the committed CSVs, and the provenance must match the MEMBER_A F2 specification.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb

from jp_a.domain.entities import FEATURE_NAMES, ModelInfo
from jp_a.ml.artefacts import DISCLAIMER, META_KEYS, MODEL_VERSION, TARGET, artefact_path, meta_path
from jp_a.ml.evaluate import error_metrics, evaluate_against_baseline
from jp_a.ml.features import CSV_COLUMNS, load_training_frame
from jp_a.ml.train import DEFAULT_VALIDATION_SERVICE_AREAS, holdout_validation, predict_frame, train_model
from jp_a.repositories.model_repo import ModelRepository

MEMBER_A_DIR = Path(__file__).resolve().parents[2]
MODELS_DIR = MEMBER_A_DIR / "models"
TRAINING_DIR = MEMBER_A_DIR / "data" / "training"
TRAIN_CSV = TRAINING_DIR / "train.csv"
TEST_CSV = TRAINING_DIR / "test.csv"

MAX_ARTEFACT_BYTES = 2 * 1024 * 1024
SPEC_HYPERPARAMETERS = {
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "random_state": 42,
}
PROVENANCE_KEYS = set(META_KEYS) | {"validation"}
# Metrics are stored at 1 dp; float32 inference may move the last digit by one step.
METRIC_TOLERANCE = 0.15


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads(meta_path(MODELS_DIR).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def train_frame() -> pd.DataFrame:
    return load_training_frame(TRAIN_CSV)


@pytest.fixture(scope="module")
def test_frame() -> pd.DataFrame:
    return load_training_frame(TEST_CSV)


@pytest.fixture(scope="module")
def booster() -> xgb.Booster:
    b = xgb.Booster()
    b.load_model(str(artefact_path(MODELS_DIR)))
    return b


def _csv_rows(path: Path) -> int:
    with open(path, encoding="utf-8") as fh:
        return sum(1 for _ in fh) - 1  # minus the header


# --------------------------------------------------------------------------- files
def test_artefacts_exist_and_are_small():
    model_file, meta_file = artefact_path(MODELS_DIR), meta_path(MODELS_DIR)
    assert model_file.is_file() and meta_file.is_file()
    assert model_file.stat().st_size < MAX_ARTEFACT_BYTES
    assert meta_file.stat().st_size < 64 * 1024


def test_meta_is_utf8_lf_json():
    raw = meta_path(MODELS_DIR).read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------- meta shape
def test_meta_parses_into_strict_model_info(meta):
    info = ModelInfo.model_validate({k: meta[k] for k in ModelInfo.model_fields})
    assert info.model_version == MODEL_VERSION
    assert info.target == TARGET
    assert info.is_simulation is True
    assert info.disclaimer == DISCLAIMER


def test_meta_carries_only_known_keys(meta):
    assert set(META_KEYS) <= set(meta)
    assert set(meta) <= PROVENANCE_KEYS


def test_meta_provenance_matches_spec(meta):
    assert meta["feature_names"] == list(FEATURE_NAMES)
    assert meta["xgboost_version"] == xgb.__version__
    assert meta["seed"] == 42
    for name, value in SPEC_HYPERPARAMETERS.items():
        assert meta["hyperparameters"][name] == value, name


def test_trained_rows_match_csv_row_counts(meta, train_frame, test_frame):
    assert meta["train_rows"] == _csv_rows(TRAIN_CSV) == len(train_frame)
    assert meta["test_rows"] == _csv_rows(TEST_CSV) == len(test_frame)


def test_csv_header_is_the_training_contract():
    for path in (TRAIN_CSV, TEST_CSV):
        with open(path, encoding="utf-8") as fh:
            assert fh.readline().rstrip("\n").split(",") == CSV_COLUMNS


def test_split_is_by_service_area_and_excludes_demo(train_frame, test_frame):
    train_ids, test_ids = set(train_frame["service_area_id"]), set(test_frame["service_area_id"])
    assert train_ids == {f"sa_{i:02d}" for i in range(2, 34)}
    assert test_ids == {f"sa_{i:02d}" for i in range(34, 42)}
    assert "sa_01" not in train_ids | test_ids


# --------------------------------------------------------------------------- importances
def test_feature_importances_cover_all_features_and_sum_to_one(meta):
    importances = meta["feature_importances"]
    assert sorted(f["feature"] for f in importances) == sorted(FEATURE_NAMES)
    values = [f["importance"] for f in importances]
    assert all(0.0 <= v <= 1.0 for v in values)
    assert values == sorted(values, reverse=True)
    assert math.isclose(sum(values), 1.0, abs_tol=5e-4)  # nine values each rounded to 4 dp


def test_feature_importances_match_the_booster(meta, booster):
    gains = booster.get_score(importance_type="gain")
    total = sum(gains.values())
    stored = {f["feature"]: f["importance"] for f in meta["feature_importances"]}
    for name in FEATURE_NAMES:
        assert stored[name] == pytest.approx(gains.get(name, 0.0) / total, abs=1e-4), name


# --------------------------------------------------------------------------- metrics
def test_baseline_metrics_recompute_exactly(meta, test_frame):
    y_true = test_frame[TARGET].to_numpy(dtype=np.float64)
    baseline = error_metrics(y_true, test_frame["last_need_litres"].to_numpy(dtype=np.float64))
    assert baseline == meta["metrics"]["last_observed_baseline"]


def test_committed_booster_reproduces_stored_test_metrics(meta, booster, test_frame):
    recomputed = evaluate_against_baseline(test_frame, predict_frame(booster, test_frame))
    for model in ("xgboost", "last_observed_baseline"):
        for metric in ("mae_litres", "rmse_litres"):
            assert recomputed[model][metric] == pytest.approx(meta["metrics"][model][metric], abs=METRIC_TOLERANCE)


def test_model_repository_serves_the_same_predictions(settings, booster, test_frame):
    repo = ModelRepository(settings.model_copy(update={"a_models_dir": MODELS_DIR}))
    predictions, contribs = repo.predict(test_frame[list(FEATURE_NAMES)])
    np.testing.assert_allclose(predictions, predict_frame(booster, test_frame), rtol=0, atol=1e-3)
    # SHAP contributions plus bias add up to the prediction (litres).
    np.testing.assert_allclose(contribs.sum(axis=1), predictions, rtol=1e-5, atol=1.0)
    assert repo.get_model_info().metrics.model_dump() == json.loads(meta_path(MODELS_DIR).read_text(encoding="utf-8"))["metrics"]


def test_xgboost_beats_the_last_observed_baseline(meta):
    xgb_m, base_m = meta["metrics"]["xgboost"], meta["metrics"]["last_observed_baseline"]
    assert xgb_m["mae_litres"] < base_m["mae_litres"]
    assert xgb_m["rmse_litres"] < base_m["rmse_litres"]
    assert xgb_m["rmse_litres"] >= xgb_m["mae_litres"] > 0


# --------------------------------------------------------------------------- validation (train split only)
def test_validation_block_uses_training_service_areas_only(meta, train_frame, test_frame):
    validation = meta["validation"]
    holdout = set(validation["holdout_service_areas"])
    assert holdout == set(DEFAULT_VALIDATION_SERVICE_AREAS)
    assert holdout <= set(train_frame["service_area_id"])
    assert not holdout & set(test_frame["service_area_id"])
    is_holdout = train_frame["service_area_id"].isin(holdout)
    assert validation["holdout_rows"] == int(is_holdout.sum())
    assert validation["fit_rows"] + validation["holdout_rows"] == meta["train_rows"]
    assert validation["metrics"]["xgboost"]["mae_litres"] < validation["metrics"]["last_observed_baseline"]["mae_litres"]


def test_validation_metrics_recompute(meta, train_frame):
    recomputed = holdout_validation(train_frame, meta["validation"]["holdout_service_areas"])
    for model in ("xgboost", "last_observed_baseline"):
        for metric in ("mae_litres", "rmse_litres"):
            stored = meta["validation"]["metrics"][model][metric]
            assert recomputed["metrics"][model][metric] == pytest.approx(stored, abs=METRIC_TOLERANCE)


# --------------------------------------------------------------------------- reproducibility
def test_retraining_reproduces_the_committed_meta(meta, tmp_path):
    fresh = train_model(TRAIN_CSV, TEST_CSV, tmp_path, validation_service_areas=DEFAULT_VALIDATION_SERVICE_AREAS)
    assert fresh["train_rows"] == meta["train_rows"] and fresh["test_rows"] == meta["test_rows"]
    assert fresh["hyperparameters"] == meta["hyperparameters"]
    for model in ("xgboost", "last_observed_baseline"):
        for metric in ("mae_litres", "rmse_litres"):
            assert fresh["metrics"][model][metric] == pytest.approx(meta["metrics"][model][metric], abs=METRIC_TOLERANCE)
    stored = {f["feature"]: f["importance"] for f in meta["feature_importances"]}
    for item in fresh["feature_importances"]:
        assert item["importance"] == pytest.approx(stored[item["feature"]], abs=2e-3)
