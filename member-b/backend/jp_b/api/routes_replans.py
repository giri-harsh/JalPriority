from fastapi import APIRouter, Depends, status

from jp_b.api.deps import get_current_user, get_replan_service
from jp_b.api.schemas import ReplanRequest, ReplanResponse
from jp_b.services.replan_service import ReplanService

router = APIRouter(tags=["Replans"])


@router.post("/b/v1/replans", response_model=ReplanResponse, status_code=status.HTTP_201_CREATED)
async def apply_replan(
    req: ReplanRequest,
    user: dict = Depends(get_current_user),
    replan_service: ReplanService = Depends(get_replan_service),
):
    officer_id = user.get("sub", "officer_01")
    return await replan_service.replan(
        plan_id=req.plan_id,
        disruption=req.disruption,
        officer_id=officer_id,
    )
