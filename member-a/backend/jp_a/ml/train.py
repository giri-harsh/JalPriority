"""Train the shortage model (MEMBER_A F2) and write ``models/shortage_xgb_v1.json`` + ``model_meta.json``.

Usage::

    python -m jp_a.ml.train                       # data/training/{train,test}.csv -> models/
    python -m jp_a.ml.train --validate            # also report a train-only holdout check (sa_30..sa_33)
    python -m jp_a.ml.train --train-csv a.csv --test-csv b.csv --models-dir out/

The test split is used exactly once, for the reported metrics. Any model comparison or tuning must use
``holdout_validation`` (service areas carved out of the training split), never the test split.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

from ..domain.entities import FEATURE_NAMES, SCHEMA_VERSION, now_iso
from .artefacts import DISCLAIMER, MODEL_VERSION, TARGET, artefact_path, meta_path, write_json_atomic
from .evaluate import evaluate_against_baseline
from .features import FEATURE_COLUMNS, load_training_frame, to_model_matrix

DEFAULT_SEED = 42
DEFAULT_HYPERPARAMETERS: dict[str, Any] = {
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "objective": "reg:squarederror",
    "tree_method": "hist",
}
DEFAULT_VALIDATION_SERVICE_AREAS: tuple[str, ...] = ("sa_30", "sa_31", "sa_32", "sa_33")
DEMO_SERVICE_AREA = "sa_01"  # the demo service area is never used for training or evaluation


def _check_split(train: pd.DataFrame, test: pd.DataFrame) -> None:
    train_ids, test_ids = set(train["service_area_id"]), set(test["service_area_id"])
    if DEMO_SERVICE_AREA in train_ids | test_ids:
        raise ValueError(f"{DEMO_SERVICE_AREA} is the demo service area and must not appear in training data")
    overlap = sorted(train_ids & test_ids)
    if overlap:
        raise ValueError(f"train and test splits share service areas {overlap}; the split must be by service area")


def fit_regressor(frame: pd.DataFrame, hyperparameters: Mapping[str, Any], seed: int) -> xgb.XGBRegressor:
    model = xgb.XGBRegressor(**dict(hyperparameters), random_state=seed)
    model.fit(to_model_matrix(frame), frame[TARGET].to_numpy(dtype=np.float64))
    model.get_booster().feature_names = list(FEATURE_COLUMNS)
    return model


def predict_frame(booster: xgb.Booster, frame: pd.DataFrame) -> np.ndarray:
    return booster.predict(xgb.DMatrix(to_model_matrix(frame), feature_names=list(FEATURE_COLUMNS)))


def gain_importances(booster: xgb.Booster) -> list[dict[str, float | str]]:
    """Gain importance for all nine features (unused ones get 0), normalised to sum 1, sorted descending."""
    scores = booster.get_score(importance_type="gain")
    gains = np.array([float(scores.get(name, 0.0)) for name in FEATURE_NAMES])
    total = gains.sum()
    shares = gains / total if total > 0 else np.zeros_like(gains)
    order = sorted(range(len(FEATURE_NAMES)), key=lambda i: (-shares[i], i))
    return [{"feature": FEATURE_NAMES[i], "importance": round(float(shares[i]), 4)} for i in order]


def holdout_validation(
    train: pd.DataFrame,
    holdout_service_areas: Sequence[str] = DEFAULT_VALIDATION_SERVICE_AREAS,
    hyperparameters: Mapping[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Fit on the training split minus ``holdout_service_areas`` and score both models on the holdout.

    This is the only sanctioned place to compare features or hyperparameters; the test split stays untouched.
    """
    holdout_ids = set(holdout_service_areas)
    missing = sorted(holdout_ids - set(train["service_area_id"]))
    if missing:
        raise ValueError(f"holdout service areas not present in the training split: {missing}")
    is_holdout = train["service_area_id"].isin(holdout_ids)
    fit_part, holdout = train[~is_holdout], train[is_holdout]
    if fit_part.empty:
        raise ValueError("holdout leaves no rows to fit on")
    model = fit_regressor(fit_part, hyperparameters or DEFAULT_HYPERPARAMETERS, seed)
    metrics = evaluate_against_baseline(holdout, predict_frame(model.get_booster(), holdout))
    return {
        "holdout_service_areas": sorted(holdout_ids),
        "fit_rows": int(len(fit_part)),
        "holdout_rows": int(len(holdout)),
        "metrics": metrics,
    }


def train_model(
    train_csv: Path | str,
    test_csv: Path | str,
    models_dir: Path | str,
    seed: int = DEFAULT_SEED,
    hyperparameters: Mapping[str, Any] | None = None,
    validation_service_areas: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Train on ``train_csv``, evaluate once on ``test_csv``, write the booster and meta; return the meta dict.

    ``validation_service_areas`` (optional) adds a ``validation`` block computed on training service areas only.
    """
    train = load_training_frame(train_csv)
    test = load_training_frame(test_csv)
    _check_split(train, test)
    params = dict(hyperparameters or DEFAULT_HYPERPARAMETERS)

    validation = holdout_validation(train, validation_service_areas, params, seed) if validation_service_areas else None

    model = fit_regressor(train, params, seed)
    booster = model.get_booster()
    metrics = evaluate_against_baseline(test, predict_frame(booster, test))

    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "trained_at": now_iso(),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "target": TARGET,
        "feature_names": list(FEATURE_NAMES),
        "hyperparameters": {**params, "random_state": seed},
        "seed": seed,
        "xgboost_version": xgb.__version__,
        "metrics": metrics,
        "feature_importances": gain_importances(booster),
        "is_simulation": True,
        "disclaimer": DISCLAIMER,
    }
    if validation is not None:
        meta["validation"] = validation

    out_dir = Path(models_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_file = artefact_path(out_dir)
    tmp_model = model_file.with_name(model_file.stem + ".tmp.json")
    booster.save_model(str(tmp_model))
    tmp_model.replace(model_file)
    write_json_atomic(meta_path(out_dir), meta)
    return meta


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    from ..config import Settings

    settings = Settings()
    parser = argparse.ArgumentParser(prog="python -m jp_a.ml.train", description="Train the JalPriority shortage model.")
    parser.add_argument("--train-csv", type=Path, default=settings.training_dir / "train.csv")
    parser.add_argument("--test-csv", type=Path, default=settings.training_dir / "test.csv")
    parser.add_argument("--models-dir", type=Path, default=Path(settings.a_models_dir))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--validate",
        action="store_true",
        help=f"also fit on train minus {','.join(DEFAULT_VALIDATION_SERVICE_AREAS)} and record holdout metrics",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        meta = train_model(
            args.train_csv,
            args.test_csv,
            args.models_dir,
            seed=args.seed,
            validation_service_areas=DEFAULT_VALIDATION_SERVICE_AREAS if args.validate else None,
        )
    except (OSError, ValueError) as exc:
        print(f"training failed: {exc}", file=sys.stderr)
        return 1
    summary = {
        "models_dir": str(args.models_dir),
        "train_rows": meta["train_rows"],
        "test_rows": meta["test_rows"],
        "metrics": meta["metrics"],
        "beats_baseline_mae": meta["metrics"]["xgboost"]["mae_litres"] < meta["metrics"]["last_observed_baseline"]["mae_litres"],
        "top_features": meta["feature_importances"][:3],
    }
    if "validation" in meta:
        summary["validation"] = meta["validation"]
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
