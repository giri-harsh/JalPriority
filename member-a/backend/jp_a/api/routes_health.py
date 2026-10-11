"""6.1 GET /a/v1/health (no auth)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import MODEL_VERSION, __version__
from ..domain.entities import HealthResponse, now_iso
from .deps import Container, get_container

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(c: Container = Depends(get_container)) -> HealthResponse:
    try:
        model_version = str(c.model_repo.model_version)
    except Exception:  # noqa: BLE001 - health must never fail
        model_version = MODEL_VERSION
    return HealthResponse(version=__version__, model_version=model_version, time=now_iso())
