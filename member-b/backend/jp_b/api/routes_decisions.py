from fastapi import APIRouter, Depends

from jp_b.api.deps import get_current_user, get_decision_service
from jp_b.api.schemas import PlanDecisionRequest, PlanDecisionResponse
from jp_b.services.decision_service import DecisionService

router = APIRouter(tags=["Decisions"])


@router.post("/b/v1/plans/{plan_id}/decision", response_model=PlanDecisionResponse)
async def make_plan_decision(
    plan_id: str,
    req: PlanDecisionRequest,
    user: dict = Depends(get_current_user),
    decision_service: DecisionService = Depends(get_decision_service),
):
    officer_id = user.get("sub", "officer_01")
    return await decision_service.apply_decision(
        plan_id=plan_id,
        req=req,
        officer_id=officer_id,
    )
