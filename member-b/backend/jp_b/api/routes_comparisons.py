from fastapi import APIRouter, Depends

from jp_b.api.deps import get_current_user, get_comparison_service
from jp_b.api.schemas import ComparisonRequest, ComparisonResponse
from jp_b.services.comparison_service import ComparisonService

router = APIRouter(tags=["Comparisons"])


@router.post("/b/v1/comparisons", response_model=ComparisonResponse)
async def run_comparison(
    req: ComparisonRequest,
    user: dict = Depends(get_current_user),
    comparison_service: ComparisonService = Depends(get_comparison_service),
):
    officer_id = user.get("sub", "officer_01")
    return await comparison_service.run_comparison(
        scenario_id=req.scenario_id,
        request_weights=req.weights,
        officer_id=officer_id,
    )
