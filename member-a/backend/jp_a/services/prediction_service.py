"""Prediction service. OWNER: Agent ML.

INTERFACE (frozen by LEAD; PlanService + routes + tests rely on it):

    PredictionService(settings, scenario_repo, model_repo)
        get_predictions(scenario_id: str) -> PredictionSet     # loads scenario (ScenarioNotFoundError), cached per scenario_id
        predict_for_scenario(scenario: Scenario) -> PredictionSet   # cached by scenario.scenario_id

Rules: F-1 shortfall = max(0, predicted_need - expected_piped) (int); top_features = 3 largest |SHAP| (litres) via
pred_contribs; run_mae_litres from repo.get_truth() for seeds, None for derived (parent_scenario_id is not None);
model unavailable -> ModelUnavailableError. latency_ms measured. Areas in scenario order.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Sequence

import numpy as np

from ..api.errors import ContractValidationError
from ..domain.entities import (
    FEATURE_NAMES,
    SCHEMA_VERSION,
    AreaPrediction,
    FeatureContribution,
    PredictionSet,
    Scenario,
    now_iso,
)
from ..logging_setup import log_event
from ..ml.features import FeatureError, build_feature_frame

TOP_FEATURES = 3


def shortfall_litres(predicted_need_litres: int, expected_piped_litres: int) -> int:
    """CONTRACT F-1: ``max(0, predicted_need - expected_piped)``."""
    return max(0, int(predicted_need_litres) - int(expected_piped_litres))


def top_contributions(contribs_row: Sequence[float] | np.ndarray, k: int = TOP_FEATURES) -> list[FeatureContribution]:
    """The ``k`` features with the largest |SHAP contribution| (bias column excluded), sorted descending.

    Ties keep ``FEATURE_NAMES`` order so the output is deterministic. Values are litres rounded to 1 dp.
    """
    values = np.asarray(contribs_row, dtype=np.float64)[: len(FEATURE_NAMES)]
    order = sorted(range(len(FEATURE_NAMES)), key=lambda i: (-abs(values[i]), i))[:k]
    return [
        FeatureContribution(feature=FEATURE_NAMES[i], contribution_litres=round(float(values[i]), 1) + 0.0)
        for i in order
    ]


def run_mae(predicted: dict[str, int], truth: dict | None) -> float | None:
    """MAE of predicted needs against the hidden ground truth (areas present in both), 1 dp; None without truth."""
    if not truth:
        return None
    true_need = truth.get("true_need_litres") or {}
    common = [area_id for area_id in predicted if area_id in true_need]
    if not common:
        return None
    errors = [abs(predicted[a] - float(true_need[a])) for a in common]
    return round(sum(errors) / len(errors), 1)


class PredictionService:
    """Predicts need, shortfall (F-1) and SHAP top features per area; caches one PredictionSet per scenario id."""

    def __init__(self, settings, scenario_repo, model_repo):
        self.settings = settings
        self.scenario_repo = scenario_repo
        self.model_repo = model_repo
        self._cache: dict[str, PredictionSet] = {}
        self._lock = threading.Lock()

    def get_predictions(self, scenario_id: str) -> PredictionSet:
        cached = self._cached(scenario_id)
        if cached is not None:
            return cached
        scenario = self.scenario_repo.get(scenario_id)  # ScenarioNotFoundError propagates (404)
        return self.predict_for_scenario(scenario)

    def predict_for_scenario(self, scenario: Scenario) -> PredictionSet:
        cached = self._cached(scenario.scenario_id)
        if cached is not None:
            return cached
        prediction = self._compute(scenario)
        with self._lock:
            # A concurrent request may have filled the slot first; every caller gets the same object.
            return self._cache.setdefault(scenario.scenario_id, prediction)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _cached(self, scenario_id: str) -> PredictionSet | None:
        with self._lock:
            return self._cache.get(scenario_id)

    def _compute(self, scenario: Scenario) -> PredictionSet:
        started = time.perf_counter()
        try:
            features = build_feature_frame(scenario)
        except FeatureError as exc:
            raise ContractValidationError(
                f"Scenario {scenario.scenario_id} cannot be scored: {exc}",
                details={"scenario_id": scenario.scenario_id},
            ) from exc
        predictions, contribs = self.model_repo.predict(features)  # ModelUnavailableError propagates (503)
        latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))

        areas: list[AreaPrediction] = []
        for idx, area in enumerate(scenario.areas):
            need = int(round(max(0.0, float(predictions[idx]))))
            areas.append(
                AreaPrediction(
                    area_id=area.area_id,
                    predicted_need_litres=need,
                    expected_piped_litres=area.expected_piped_litres,
                    predicted_shortfall_litres=shortfall_litres(need, area.expected_piped_litres),
                    baseline_last_need_litres=int(features.iloc[idx]["last_need_litres"]),
                    top_features=top_contributions(contribs[idx]),
                )
            )

        mae = None
        if scenario.parent_scenario_id is None:
            truth = self.scenario_repo.get_truth(scenario.scenario_id)
            mae = run_mae({a.area_id: a.predicted_need_litres for a in areas}, truth)

        result = PredictionSet(
            schema_version=SCHEMA_VERSION,
            scenario_id=scenario.scenario_id,
            model_version=self.model_repo.model_version,
            generated_at=now_iso(),
            latency_ms=latency_ms,
            run_mae_litres=mae,
            is_simulation=True,
            areas=areas,
        )
        log_event(
            "prediction_computed",
            f"predicted {len(areas)} areas for {scenario.scenario_id}",
            scenario_id=scenario.scenario_id,
            latency_ms=latency_ms,
            run_mae_litres=mae,
        )
        return result
