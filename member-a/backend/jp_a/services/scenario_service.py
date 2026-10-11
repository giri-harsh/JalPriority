"""Scenario + disruption orchestration (routes 6.2, 6.3, 6.4)."""
from __future__ import annotations

from ..domain.disruptions import apply_disruption, validate_disruption
from ..domain.entities import Scenario, ScenarioSummary, now_iso
from ..logging_setup import log_event


class ScenarioService:
    def __init__(self, settings, scenario_repo):
        self.settings = settings
        self.repo = scenario_repo

    def list_scenarios(self, include_derived: bool = False) -> list[ScenarioSummary]:
        return sorted(self.repo.list_summaries(include_derived=include_derived), key=lambda s: s.scenario_id)

    def get_scenario(self, scenario_id: str) -> Scenario:
        return self.repo.get(scenario_id)  # raises ScenarioNotFoundError

    def apply_disruption(self, scenario_id: str, dtype: str, params: dict | None) -> tuple[Scenario, bool]:
        """Returns ``(scenario, created)``. ``created`` False => the derived scenario already existed (HTTP 200)."""
        parent = self.repo.get(scenario_id)
        normalised = validate_disruption(parent, dtype, params)
        derived = apply_disruption(parent, dtype, normalised, now_iso())
        if self.repo.exists(derived.scenario_id):
            return self.repo.get(derived.scenario_id), False
        self.repo.save_derived(derived)
        log_event("disruption_applied", f"{dtype} on {scenario_id} -> {derived.scenario_id}", type=dtype)
        return derived, True
