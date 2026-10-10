"""Feature definitions shared by training (CSV rows) and serving (Scenario areas).

The nine model features are ``jp_a.domain.entities.FEATURE_NAMES`` in that exact order:

==========================  ======================================================================
``max_temp_c``              ``scenario.heat.max_temp_c`` (forecast for the window being planned)
``heat_index_c``            ``scenario.heat.heat_index_c``
``alert_level``             ``scenario.heat.alert_level`` (0-3)
``heat_exposure``           ``area.heat_exposure``
``population``              ``area.population``
``last_need_litres``        ``area.history[-1].need_litres``
``mean_need_last3_litres``  mean of the last (up to) 3 ``history[].need_litres``, rounded to 1 dp
``last_received_litres``    ``area.history[-1].received_litres``
``expected_piped_litres``   ``area.expected_piped_litres``
==========================  ======================================================================

Target: ``need_litres`` of the window being planned.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..domain.entities import FEATURE_NAMES, SERVICE_AREA_ID_RE, Area, Heat, Scenario
from .artefacts import TARGET

FEATURE_COLUMNS: list[str] = list(FEATURE_NAMES)

# Exact header of data/training/{train,test}.csv (one row per service area / area / window >= 2).
CSV_COLUMNS: list[str] = ["service_area_id", "area_id", "window_index", *FEATURE_COLUMNS, TARGET]

INT_COLUMNS: tuple[str, ...] = (
    "window_index", "alert_level", "population", "last_need_litres", "last_received_litres",
    "expected_piped_litres", TARGET,
)  # fmt: skip
FLOAT_COLUMNS: tuple[str, ...] = ("max_temp_c", "heat_index_c", "heat_exposure", "mean_need_last3_litres")

HISTORY_MEAN_WINDOWS = 3
MEAN_NEED_DECIMALS = 1  # the training CSV stores mean_need_last3_litres at 1 dp; serving matches it exactly


class FeatureError(ValueError):
    """A scenario or training file cannot be turned into model features."""


def area_features(area: Area, heat: Heat) -> dict[str, float | int]:
    """The nine features for one area of a scenario (requires at least one history window)."""
    if not area.history:
        raise FeatureError(f"area {area.area_id} has no history; lag features need at least one window")
    history = sorted(area.history, key=lambda h: h.window_index)
    last = history[-1]
    recent = history[-HISTORY_MEAN_WINDOWS:]
    return {
        "max_temp_c": float(heat.max_temp_c),
        "heat_index_c": float(heat.heat_index_c),
        "alert_level": int(heat.alert_level),
        "heat_exposure": float(area.heat_exposure),
        "population": int(area.population),
        "last_need_litres": int(last.need_litres),
        "mean_need_last3_litres": round(sum(h.need_litres for h in recent) / len(recent), MEAN_NEED_DECIMALS),
        "last_received_litres": int(last.received_litres),
        "expected_piped_litres": int(area.expected_piped_litres),
    }


def build_feature_frame(scenario: Scenario) -> pd.DataFrame:
    """One row per area in scenario order, columns in ``FEATURE_NAMES`` order, indexed by ``area_id``."""
    rows = [area_features(area, scenario.heat) for area in scenario.areas]
    frame = pd.DataFrame(rows, columns=FEATURE_COLUMNS, index=pd.Index([a.area_id for a in scenario.areas], name="area_id"))
    return _apply_dtypes(frame)


def to_model_matrix(frame: pd.DataFrame) -> np.ndarray:
    """The float64 design matrix (n, 9) in ``FEATURE_NAMES`` order; used identically for training and serving."""
    missing = [c for c in FEATURE_COLUMNS if c not in frame.columns]
    if missing:
        raise FeatureError(f"feature frame is missing columns: {missing}")
    matrix = frame[FEATURE_COLUMNS].to_numpy(dtype=np.float64)
    if not np.isfinite(matrix).all():
        raise FeatureError("feature frame contains NaN or infinite values")
    return matrix


def load_training_frame(csv_path: Path | str) -> pd.DataFrame:
    """Read and validate a training/test CSV: exact header, no missing values, integral litres, valid ids."""
    path = Path(csv_path)
    if not path.is_file():
        raise FeatureError(f"training file not found: {path}")
    frame = pd.read_csv(path, dtype={"service_area_id": str, "area_id": str})
    header = list(frame.columns)
    if header != CSV_COLUMNS:
        raise FeatureError(f"{path.name}: header {header} does not match the expected {CSV_COLUMNS}")
    if frame.empty:
        raise FeatureError(f"{path.name}: no data rows")
    if frame.isna().any().any():
        bad = [c for c in CSV_COLUMNS if frame[c].isna().any()]
        raise FeatureError(f"{path.name}: missing values in columns {bad}")
    if not frame["service_area_id"].map(lambda s: bool(SERVICE_AREA_ID_RE.match(s))).all():
        raise FeatureError(f"{path.name}: malformed service_area_id values")
    frame = _apply_dtypes(frame)
    if (frame["window_index"] < 2).any():
        raise FeatureError(f"{path.name}: rows need window_index >= 2 (lag features require history)")
    return frame


def _apply_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for col in FLOAT_COLUMNS:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="raise").astype(np.float64)
    for col in INT_COLUMNS:
        if col in frame.columns:
            values = pd.to_numeric(frame[col], errors="raise").astype(np.float64)
            if not np.equal(np.round(values), values).all():
                raise FeatureError(f"column {col} must hold integer values")
            frame[col] = values.astype(np.int64)
    return frame
