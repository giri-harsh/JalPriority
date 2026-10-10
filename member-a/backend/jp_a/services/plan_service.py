"""Plan service. OWNER: Agent OPT.

INTERFACE (frozen by LEAD):

    PlanService(settings, scenario_repo, prediction_service)
        solve(request: SolveRequest) -> PlanResult
            # 404 ScenarioNotFoundError; unknown debt_by_area area ids -> ContractValidationError (422);
            # time_limit_s None -> settings.a_solver_time_limit_s; NEVER an error for a time limit.
        build_plan(scenario, prediction, weights, debt_by_area, time_limit_s) -> PlanResult
            # PURE (no repo / model access) - the golden test and property tests call this directly.
"""
from __future__ import annotations

from ..domain.entities import PlanResult, SolveRequest


class PlanService:
    """STUB - Agent OPT implements."""

    def __init__(self, settings, scenario_repo, prediction_service):
        self.settings = settings
        self.scenario_repo = scenario_repo
        self.prediction_service = prediction_service

    def solve(self, request: SolveRequest) -> PlanResult:  # pragma: no cover
        raise NotImplementedError
