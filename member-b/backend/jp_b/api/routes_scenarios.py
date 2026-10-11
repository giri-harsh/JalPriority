from fastapi import APIRouter, Depends, Query
from typing import Dict, Any

from jp_b.api.deps import get_current_user, get_a_client
from jp_b.clients.a_client import AClient
from jp_b.api.schemas import (
    Scenario,
    PredictionSet,
    ScenarioSummaryList
)

router = APIRouter(tags=["Scenarios"])

@router.get("/b/v1/scenarios", response_model=ScenarioSummaryList)
async def list_scenarios(
    include_derived: bool = Query(default=False),
    user: dict = Depends(get_current_user),
    client: AClient = Depends(get_a_client)
):
    scenarios = await client.list_scenarios(include_derived=include_derived)
    return {"scenarios": scenarios}

@router.get("/b/v1/scenarios/{scenario_id}", response_model=Scenario)
async def get_scenario(
    scenario_id: str,
    user: dict = Depends(get_current_user),
    client: AClient = Depends(get_a_client)
):
    return await client.get_scenario(scenario_id)

@router.get("/b/v1/scenarios/{scenario_id}/predictions", response_model=PredictionSet)
async def get_predictions(
    scenario_id: str,
    user: dict = Depends(get_current_user),
    client: AClient = Depends(get_a_client)
):
    return await client.get_predictions(scenario_id)
