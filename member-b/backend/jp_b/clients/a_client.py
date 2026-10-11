from typing import Protocol, List, Dict, Any, Union, Optional
from jp_b.api.schemas import (
    Scenario,
    ScenarioSummary,
    PredictionSet,
    ModelInfo,
    PlanResult,
    SolveRequest,
    Disruption
)

class AClient(Protocol):
    """Protocol for Member A client (HTTP or Mock)."""

    async def health(self) -> Dict[str, Any]:
        """Check upstream Member A health."""
        ...

    async def list_scenarios(self, include_derived: bool = False) -> List[ScenarioSummary]:
        """List scenarios summaries."""
        ...

    async def get_scenario(self, scenario_id: str) -> Scenario:
        """Fetch full Scenario by ID."""
        ...

    async def apply_disruption(
        self,
        scenario_id: str,
        disruption: Union[Disruption, Dict[str, Any]]
    ) -> Scenario:
        """Apply a disruption to a scenario, returning the derived scenario."""
        ...

    async def get_predictions(self, scenario_id: str) -> PredictionSet:
        """Fetch PredictionSet for a scenario."""
        ...

    async def solve(
        self,
        solve_request: Union[SolveRequest, Dict[str, Any]]
    ) -> PlanResult:
        """Request solution for a scenario with weights and debt."""
        ...

    async def get_model_info(self) -> ModelInfo:
        """Fetch model metadata and training info."""
        ...
