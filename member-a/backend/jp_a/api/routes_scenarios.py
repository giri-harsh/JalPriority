"""6.2 GET /scenarios, 6.3 GET /scenarios/{id}, 6.4 POST /scenarios/{id}/disruptions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ..domain.entities import Scenario, ScenarioList
from .auth import require_auth
from .deps import Container, get_container
from .schemas import DisruptionRequestIn

router = APIRouter(tags=["scenarios"], dependencies=[Depends(require_auth)])


@router.get("/scenarios", response_model=ScenarioList)
def list_scenarios(include_derived: bool = False, c: Container = Depends(get_container)) -> ScenarioList:
    return ScenarioList(scenarios=c.scenario_service.list_scenarios(include_derived=include_derived))


@router.get("/scenarios/{scenario_id}", response_model=Scenario)
def get_scenario(scenario_id: str, c: Container = Depends(get_container)) -> Scenario:
    return c.scenario_service.get_scenario(scenario_id)


@router.post("/scenarios/{scenario_id}/disruptions", response_model=Scenario, status_code=201)
def apply_disruption(
    scenario_id: str, body: DisruptionRequestIn, response: Response, c: Container = Depends(get_container)
) -> Scenario:
    scenario, created = c.scenario_service.apply_disruption(scenario_id, body.type, body.params)
    response.status_code = 201 if created else 200
    return scenario
