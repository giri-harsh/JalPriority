"""Error metrics and the "last observed need" baseline (CONTRACT A4 / 5.5)."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from .artefacts import TARGET

ArrayLike = Sequence[float] | np.ndarray | pd.Series


def _pair(y_true: ArrayLike, y_pred: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(y_true, dtype=np.float64)
    p = np.asarray(y_pred, dtype=np.float64)
    if t.shape != p.shape or t.ndim != 1 or t.size == 0:
        raise ValueError(f"y_true and y_pred must be equal-length non-empty 1-D arrays, got {t.shape} and {p.shape}")
    return t, p


def mae(y_true: ArrayLike, y_pred: ArrayLike) -> float:
    t, p = _pair(y_true, y_pred)
    return float(np.mean(np.abs(t - p)))


def rmse(y_true: ArrayLike, y_pred: ArrayLike) -> float:
    t, p = _pair(y_true, y_pred)
    return float(np.sqrt(np.mean((t - p) ** 2)))


def error_metrics(y_true: ArrayLike, y_pred: ArrayLike) -> dict[str, float]:
    """``{"mae_litres", "rmse_litres"}`` rounded to 1 dp, the ModelInfo ``ErrorMetrics`` shape."""
    return {"mae_litres": round(mae(y_true, y_pred), 1), "rmse_litres": round(rmse(y_true, y_pred), 1)}


def last_observed_baseline(frame: pd.DataFrame) -> np.ndarray:
    """Baseline prediction: the need observed in the previous window (``last_need_litres``)."""
    return frame["last_need_litres"].to_numpy(dtype=np.float64)


def evaluate_against_baseline(frame: pd.DataFrame, model_predictions: ArrayLike) -> dict[str, dict[str, float]]:
    """ModelInfo ``metrics`` block: the model and the last-observed baseline on the same rows."""
    y_true = frame[TARGET].to_numpy(dtype=np.float64)
    return {
        "xgboost": error_metrics(y_true, model_predictions),
        "last_observed_baseline": error_metrics(y_true, last_observed_baseline(frame)),
    }
