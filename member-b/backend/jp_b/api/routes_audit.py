from typing import Optional
from fastapi import APIRouter, Depends, Query

from jp_b.api.deps import get_current_user, get_audit_service
from jp_b.api.schemas import AuditListResponse
from jp_b.services.audit_service import AuditService

router = APIRouter(tags=["Audit"])


@router.get("/b/v1/audit", response_model=AuditListResponse)
async def list_audit_entries(
    limit: int = Query(default=50),
    plan_id: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    entries = audit_service.list_entries(
        plan_id=plan_id,
        limit=limit,
    )
    return {"entries": entries}
