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

from ..domain.entities import PredictionSet, Scenario


class PredictionService:
    """STUB - Agent ML implements."""

    def __init__(self, settings, scenario_repo, model_repo):
        self.settings = settings
        self.scenario_repo = scenario_repo
        self.model_repo = model_repo

    def get_predictions(self, scenario_id: str) -> PredictionSet:  # pragma: no cover
        raise NotImplementedError

    def predict_for_scenario(self, scenario: Scenario) -> PredictionSet:  # pragma: no cover
        raise NotImplementedError
