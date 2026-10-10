"""M3: evaluation helpers, baseline, training pipeline, artefact + meta, CLI."""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb

from jp_a.domain.entities import FEATURE_NAMES, ISO_DT_PATTERN, ModelInfo
from jp_a.ml.artefacts import ARTEFACT_FILENAME, DISCLAIMER, META_FILENAME, META_KEYS
from jp_a.ml.evaluate import error_metrics, evaluate_against_baseline, last_observed_baseline, mae, rmse
from jp_a.ml.features import load_training_frame
from jp_a.ml.train import DEFAULT_HYPERPARAMETERS, holdout_validation, main, train_model

from .ml_helpers import TRAIN_SERVICE_AREAS, write_synthetic_split, write_training_csv


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    base = tmp_path_factory.mktemp("ml_train")
    train_csv, test_csv = write_synthetic_split(base / "training")
    models_dir = base / "models"
    meta = train_model(train_csv, test_csv, models_dir)
    return {"train_csv": train_csv, "test_csv": test_csv, "models_dir": models_dir, "meta": meta}


# --------------------------------------------------------------------------- evaluate.py
def test_mae_and_rmse_known_values():
    assert mae([10, 20, 30], [12, 18, 30]) == pytest.approx(4 / 3)
    assert rmse([10, 20, 30], [12, 18, 30]) == pytest.approx(math.sqrt(8 / 3))
    assert error_metrics([0, 0], [3, -4]) == {"mae_litres": 3.5, "rmse_litres": round(math.sqrt(12.5), 1)}


@pytest.mark.parametrize("a,b", [([], []), ([1, 2], [1]), ([[1]], [[1]])])
def test_metrics_reject_bad_shapes(a, b):
    with pytest.raises(ValueError):
        mae(a, b)


def test_last_observed_baseline_is_last_need():
    frame = pd.DataFrame({"last_need_litres": [100, 200, 300], "need_litres": [110, 190, 330]})
    np.testing.assert_array_equal(last_observed_baseline(frame), [100.0, 200.0, 300.0])
    metrics = evaluate_against_baseline(frame, [110, 190, 330])
    assert metrics["xgboost"] == {"mae_litres": 0.0, "rmse_litres": 0.0}
    assert metrics["last_observed_baseline"]["mae_litres"] == pytest.approx(round(50 / 3, 1))


# --------------------------------------------------------------------------- train_model
def test_artefacts_written(trained):
    models_dir = trained["models_dir"]
    assert sorted(p.name for p in models_dir.iterdir()) == sorted([ARTEFACT_FILENAME, META_FILENAME])
    raw = (models_dir / META_FILENAME).read_bytes()
    assert b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8")) == trained["meta"]


def test_meta_schema(trained):
    meta = trained["meta"]
    assert set(META_KEYS) <= set(meta)
    assert meta["schema_version"] == "1.0"
    assert meta["model_version"] == "xgb-sa-v1"
    assert meta["target"] == "need_litres"
    assert meta["feature_names"] == list(FEATURE_NAMES)
    assert meta["seed"] == 42
    assert meta["xgboost_version"] == xgb.__version__
    assert meta["is_simulation"] is True
    assert meta["disclaimer"] == DISCLAIMER
    assert meta["hyperparameters"] == {**DEFAULT_HYPERPARAMETERS, "random_state": 42}
    assert {k: meta["hyperparameters"][k] for k in ("n_estimators", "max_depth", "learning_rate", "subsample", "colsample_bytree")} == {
        "n_estimators": 300, "max_depth": 4, "learning_rate": 0.05, "subsample": 0.9, "colsample_bytree": 0.9,
    }  # fmt: skip
    assert re.match(ISO_DT_PATTERN, meta["trained_at"])
    assert meta["train_rows"] == len(load_training_frame(trained["train_csv"]))
    assert meta["test_rows"] == len(load_training_frame(trained["test_csv"]))
    assert "validation" not in meta


def test_meta_parses_as_strict_model_info(trained):
    info = ModelInfo.model_validate({k: trained["meta"][k] for k in ModelInfo.model_fields})
    assert info.is_simulation is True


def test_metrics_are_sane_and_honest(trained):
    test = load_training_frame(trained["test_csv"])
    metrics = trained["meta"]["metrics"]
    for block in ("xgboost", "last_observed_baseline"):
        m = metrics[block]
        assert set(m) == {"mae_litres", "rmse_litres"}
        assert 0 < m["mae_litres"] <= m["rmse_litres"]
        assert m["mae_litres"] == round(m["mae_litres"], 1)
    # The reported baseline is recomputable from the test split alone.
    assert metrics["last_observed_baseline"] == error_metrics(test["need_litres"], test["last_need_litres"])
    # The reported model metrics are exactly what the saved booster scores on the test split.
    booster = xgb.Booster()
    booster.load_model(str(trained["models_dir"] / ARTEFACT_FILENAME))
    preds = booster.predict(xgb.DMatrix(test[list(FEATURE_NAMES)].to_numpy(float), feature_names=list(FEATURE_NAMES)))
    assert metrics["xgboost"] == error_metrics(test["need_litres"], preds)
    # On this synthetic process the heat signal is learnable, so the model must beat persistence.
    assert metrics["xgboost"]["mae_litres"] < metrics["last_observed_baseline"]["mae_litres"]


def test_feature_importances_cover_all_features(trained):
    imps = trained["meta"]["feature_importances"]
    assert sorted(i["feature"] for i in imps) == sorted(FEATURE_NAMES)
    values = [i["importance"] for i in imps]
    assert values == sorted(values, reverse=True)
    assert all(0.0 <= v <= 1.0 for v in values)
    assert sum(values) == pytest.approx(1.0, abs=5e-4)
    assert all(v == round(v, 4) for v in values)


def test_booster_round_trip(trained):
    booster = xgb.Booster()
    booster.load_model(str(trained["models_dir"] / ARTEFACT_FILENAME))
    assert booster.feature_names == list(FEATURE_NAMES)
    assert booster.num_features() == 9


def test_training_is_deterministic(trained, tmp_path):
    meta = train_model(trained["train_csv"], trained["test_csv"], tmp_path)
    assert meta["metrics"] == trained["meta"]["metrics"]
    assert meta["feature_importances"] == trained["meta"]["feature_importances"]


def test_demo_service_area_is_refused(tmp_path):
    train = write_training_csv(tmp_path / "train.csv", ["sa_01", "sa_02"], seed=1, n_areas=2, n_windows=4)
    test = write_training_csv(tmp_path / "test.csv", ["sa_34"], seed=2, n_areas=2, n_windows=4)
    with pytest.raises(ValueError, match="sa_01"):
        train_model(train, test, tmp_path / "models")
    assert not (tmp_path / "models").exists()


def test_overlapping_split_is_refused(tmp_path):
    train = write_training_csv(tmp_path / "train.csv", ["sa_02", "sa_03"], seed=1, n_areas=2, n_windows=4)
    test = write_training_csv(tmp_path / "test.csv", ["sa_03"], seed=2, n_areas=2, n_windows=4)
    with pytest.raises(ValueError, match="sa_03"):
        train_model(train, test, tmp_path / "models")


def test_holdout_validation_uses_training_areas_only(trained):
    train = load_training_frame(trained["train_csv"])
    holdout = TRAIN_SERVICE_AREAS[-2:]
    result = holdout_validation(train, holdout, {**DEFAULT_HYPERPARAMETERS, "n_estimators": 50})
    assert result["holdout_service_areas"] == sorted(holdout)
    assert result["fit_rows"] + result["holdout_rows"] == len(train)
    assert set(result["metrics"]) == {"xgboost", "last_observed_baseline"}
    with pytest.raises(ValueError, match="sa_40"):
        holdout_validation(train, ["sa_40"])


def test_validation_block_is_recorded_when_requested(trained, tmp_path):
    meta = train_model(
        trained["train_csv"], trained["test_csv"], tmp_path, validation_service_areas=TRAIN_SERVICE_AREAS[-2:]
    )
    assert meta["validation"]["holdout_rows"] > 0
    assert meta["metrics"] == trained["meta"]["metrics"]  # final model is still trained on the full train split


def test_cli_main_in_process(trained, tmp_path, capsys):
    rc = main(["--train-csv", str(trained["train_csv"]), "--test-csv", str(trained["test_csv"]), "--models-dir", str(tmp_path)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["metrics"] == trained["meta"]["metrics"]
    assert summary["beats_baseline_mae"] is True
    assert (tmp_path / ARTEFACT_FILENAME).is_file()


def test_cli_reports_missing_input(tmp_path, capsys):
    rc = main(["--train-csv", str(tmp_path / "missing.csv"), "--test-csv", str(tmp_path / "x.csv"), "--models-dir", str(tmp_path / "m")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
    assert not (tmp_path / "m").exists()


def test_cli_module_end_to_end(trained, tmp_path):
    out_dir = tmp_path / "cli_models"
    proc = subprocess.run(
        [sys.executable, "-m", "jp_a.ml.train", "--train-csv", str(trained["train_csv"]),
         "--test-csv", str(trained["test_csv"]), "--models-dir", str(out_dir)],
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["train_rows"] == trained["meta"]["train_rows"]
    assert (out_dir / ARTEFACT_FILENAME).is_file() and (out_dir / META_FILENAME).is_file()
