from fastapi import APIRouter, Depends, Query

from jp_b.api.deps import get_current_user, get_weights_service
from jp_b.api.schemas import WeightsResponse, UpdateWeightsRequest
from jp_b.services.weights_service import WeightsService

router = APIRouter(tags=["Weights"])


@router.get("/b/v1/weights", response_model=WeightsResponse)
async def get_weights(
    service_area_id: str = Query(...),
    user: dict = Depends(get_current_user),
    weights_service: WeightsService = Depends(get_weights_service),
):
    return weights_service.get_weights(service_area_id)


@router.put("/b/v1/weights", response_model=WeightsResponse)
async def update_weights(
    req: UpdateWeightsRequest,
    user: dict = Depends(get_current_user),
    weights_service: WeightsService = Depends(get_weights_service),
):
    officer_id = user.get("sub", "officer_01")
    return weights_service.save_weights(
        service_area_id=req.service_area_id,
        new_weights=req.weights,
        officer_id=officer_id,
    )
