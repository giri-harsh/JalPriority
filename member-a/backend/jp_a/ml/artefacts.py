"""Model artefact names and the ``model_meta.json`` layout shared by training and serving.

``models/shortage_xgb_v1.json`` is the XGBoost booster (``Booster.save_model`` JSON format).
``models/model_meta.json`` is written by ``jp_a.ml.train`` and holds every CONTRACT 5.5 ModelInfo field
plus training provenance (feature names, hyperparameters, seed, xgboost version).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import MODEL_VERSION

ARTEFACT_FILENAME = "shortage_xgb_v1.json"
META_FILENAME = "model_meta.json"
TARGET = "need_litres"
DISCLAIMER = "Trained and evaluated on synthetic data. Simulation results only; not real-world accuracy."

# Keys every model_meta.json must carry (ModelInfo fields + provenance).
META_KEYS: tuple[str, ...] = (
    "schema_version", "model_version", "trained_at", "train_rows", "test_rows", "target",
    "feature_names", "hyperparameters", "seed", "xgboost_version", "metrics",
    "feature_importances", "is_simulation", "disclaimer",
)  # fmt: skip

__all__ = [
    "ARTEFACT_FILENAME", "DISCLAIMER", "META_FILENAME", "META_KEYS", "MODEL_VERSION", "TARGET",
    "artefact_path", "meta_path", "write_json_atomic",
]  # fmt: skip


def artefact_path(models_dir: Path | str) -> Path:
    return Path(models_dir) / ARTEFACT_FILENAME


def meta_path(models_dir: Path | str) -> Path:
    return Path(models_dir) / META_FILENAME


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write UTF-8 JSON with LF newlines via a temp file + rename, so readers never see a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
