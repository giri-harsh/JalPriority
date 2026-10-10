"""Scenario repository. OWNER: Agent DATA.

INTERFACE (frozen by LEAD; routes, ScenarioService, PredictionService, PlanService and tests rely on it):

    ScenarioRepository(settings, storage)
        list_summaries(include_derived: bool = False) -> list[ScenarioSummary]   # sorted by scenario_id
        get(scenario_id: str) -> Scenario            # raises jp_a.api.errors.ScenarioNotFoundError
        exists(scenario_id: str) -> bool
        save_derived(scenario: Scenario) -> None     # persists to derived/<id>.json (gitignored dir)
        get_truth(scenario_id: str) -> dict | None   # hidden ground truth for seeds, else None
            truth json: {"scenario_id": str, "window_index": int, "true_need_litres": {area_id: int}}

Seeds live in <A_DATA_DIR>/scenarios/<id>.json, derived in <A_DATA_DIR>/derived/<id>.json,
truth in <A_DATA_DIR>/truth/<id>.json. Files are strict CONTRACT 5.3 Scenario JSON (no extra keys).
ScenarioSummary.label comes from a label table owned by this module (see MEMBER_A F1 / CONTRACT 5.3).
"""
from __future__ import annotations

from ..domain.entities import Scenario, ScenarioSummary


class ScenarioRepository:
    """STUB - Agent DATA implements."""

    def __init__(self, settings, storage):
        self.settings = settings
        self.storage = storage

    def list_summaries(self, include_derived: bool = False) -> list[ScenarioSummary]:  # pragma: no cover
        raise NotImplementedError

    def get(self, scenario_id: str) -> Scenario:  # pragma: no cover
        raise NotImplementedError

    def exists(self, scenario_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def save_derived(self, scenario: Scenario) -> None:  # pragma: no cover
        raise NotImplementedError

    def get_truth(self, scenario_id: str) -> dict | None:  # pragma: no cover
        raise NotImplementedError
