from typing import Optional
from fastapi import APIRouter, Depends, Query, status

from jp_b.api.deps import get_current_user, get_plan_service
from jp_b.api.schemas import (
    CreatePlanRequest,
    PlanRecord,
    PlanListResponse,
)
from jp_b.api.errors import APIError
from jp_b.services.plan_service import PlanService

router = APIRouter(tags=["Plans"])


@router.post("/b/v1/plans", response_model=PlanRecord, status_code=status.HTTP_201_CREATED)
async def create_plan(
    req: CreatePlanRequest,
    user: dict = Depends(get_current_user),
    plan_service: PlanService = Depends(get_plan_service),
):
    officer_id = user.get("sub", "officer_01")
    plan = await plan_service.generate_plan(
        scenario_id=req.scenario_id,
        request_weights=req.weights,
        time_limit_s=req.time_limit_s,
        officer_id=officer_id,
    )
    return plan


@router.get("/b/v1/plans/{plan_id}", response_model=PlanRecord)
async def get_plan(
    plan_id: str,
    user: dict = Depends(get_current_user),
    plan_service: PlanService = Depends(get_plan_service),
):
    plan = plan_service.get_plan(plan_id)
    if not plan:
        raise APIError(code="PLAN_NOT_FOUND", message=f"Plan '{plan_id}' not found.", status_code=404)
    return plan


@router.get("/b/v1/plans", response_model=PlanListResponse)
async def list_plans(
    scenario_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    latest: bool = Query(default=False),
    user: dict = Depends(get_current_user),
    plan_service: PlanService = Depends(get_plan_service),
):
    plans = plan_service.list_plans(
        scenario_id=scenario_id,
        status=status,
        latest=latest,
    )
    return {"plans": plans}
