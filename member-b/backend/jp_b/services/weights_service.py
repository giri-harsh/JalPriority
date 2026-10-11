from datetime import datetime, timezone
from typing import Optional, Dict, Any

from jp_b.api.schemas import Weights, WeightsResponse
from jp_b.config import settings
from jp_b.repositories.base import WeightsRepository
from jp_b.services.event_service import EventService
from jp_b.services.audit_service import AuditService

DEFAULT_WEIGHTS = Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)


class WeightsService:
    def __init__(
        self,
        weights_repo: WeightsRepository,
        event_service: Optional[EventService] = None,
        audit_service: Optional[AuditService] = None,
    ):
        self.weights_repo = weights_repo
        self.event_service = event_service
        self.audit_service = audit_service

    def get_weights(self, service_area_id: str) -> WeightsResponse:
        stored = self.weights_repo.get(service_area_id)
        if stored and "weights" in stored:
            weights = Weights.model_validate(stored["weights"])
            updated_at = datetime.fromisoformat(stored["updated_at"])
            updated_by = stored.get("updated_by", "system")
        else:
            weights = DEFAULT_WEIGHTS.model_copy()
            updated_at = datetime.now(timezone.utc)
            updated_by = "system"

        flags = {
            "fairness_weights": settings.b_ff_fairness_weights,
            "vulnerability": settings.b_ff_vulnerability,
            "replan": settings.b_ff_replan,
        }
        debt_policy = {
            "decay": settings.b_debt_decay,
            "cap_litres": settings.b_debt_cap_litres,
        }

        return WeightsResponse(
            service_area_id=service_area_id,
            weights=weights,
            updated_at=updated_at,
            updated_by=updated_by,
            flags=flags,
            defaults=DEFAULT_WEIGHTS,
            debt_policy=debt_policy,
        )

    def save_weights(
        self,
        service_area_id: str,
        new_weights: Weights,
        officer_id: str,
    ) -> WeightsResponse:
        current = self.get_weights(service_area_id)
        old_weights = current.weights

        now = datetime.now(timezone.utc)
        record = {
            "service_area_id": service_area_id,
            "weights": new_weights.model_dump(),
            "updated_at": now.isoformat(),
            "updated_by": officer_id,
        }
        self.weights_repo.save(service_area_id, record)

        if self.event_service:
            self.event_service.emit(
                name="weights_changed",
                scenario_id=None,
                officer_id=officer_id,
                props={
                    "old_weights": old_weights.model_dump(),
                    "new_weights": new_weights.model_dump(),
                },
            )

        if self.audit_service:
            self.audit_service.record(
                officer_id=officer_id,
                action="weights_changed",
                details={
                    "service_area_id": service_area_id,
                    "old_weights": old_weights.model_dump(),
                    "new_weights": new_weights.model_dump(),
                },
            )

        return self.get_weights(service_area_id)
