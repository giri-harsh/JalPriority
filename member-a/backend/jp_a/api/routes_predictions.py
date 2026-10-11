"""6.5 GET /scenarios/{id}/predictions. OWNER: Agent ML (thin delegation; change only if needed)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain.entities import PredictionSet
from .auth import require_auth
from .deps import Container, get_container

router = APIRouter(tags=["predictions"], dependencies=[Depends(require_auth)])


@router.get("/scenarios/{scenario_id}/predictions", response_model=PredictionSet)
def get_predictions(scenario_id: str, c: Container = Depends(get_container)) -> PredictionSet:
    return c.prediction_service.get_predictions(scenario_id)
