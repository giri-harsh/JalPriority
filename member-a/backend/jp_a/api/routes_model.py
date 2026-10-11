"""6.7 GET /model. OWNER: Agent ML (thin delegation; change only if needed)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain.entities import ModelInfo
from .auth import require_auth
from .deps import Container, get_container

router = APIRouter(tags=["model"], dependencies=[Depends(require_auth)])


@router.get("/model", response_model=ModelInfo)
def get_model(c: Container = Depends(get_container)) -> ModelInfo:
    return c.model_repo.get_model_info()
