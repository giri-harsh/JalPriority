import secrets
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from jp_b.api.schemas import AuditEntry
from jp_b.repositories.base import AuditRepository


class AuditService:
    def __init__(self, audit_repo: AuditRepository):
        self.audit_repo = audit_repo

    def record(
        self,
        officer_id: str,
        action: str,
        plan_id: Optional[str] = None,
        scenario_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            audit_id=f"aud_{secrets.token_hex(6)}",
            ts=datetime.now(timezone.utc),
            officer_id=officer_id,
            action=action,
            plan_id=plan_id,
            scenario_id=scenario_id,
            details=details or {},
        )
        self.audit_repo.save(entry.model_dump(mode="json"))
        return entry

    def list_entries(
        self,
        plan_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[AuditEntry]:
        raw_entries = self.audit_repo.list_entries(plan_id=plan_id, limit=limit)
        return [AuditEntry.model_validate(e) for e in raw_entries]
