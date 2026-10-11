from datetime import datetime, timezone
from typing import Optional

from jp_b.api.schemas import (
    PlanRecord,
    DecisionInfo,
    PlanDecisionRequest,
    PlanDecisionResponse,
)
from jp_b.api.errors import APIError
from jp_b.config import settings
from jp_b.repositories.base import PlansRepository
from jp_b.services.plan_service import PlanService
from jp_b.services.ledger_service import LedgerService
from jp_b.services.event_service import EventService
from jp_b.services.audit_service import AuditService


class DecisionService:
    def __init__(
        self,
        plans_repo: PlansRepository,
        plan_service: PlanService,
        ledger_service: LedgerService,
        event_service: EventService,
        audit_service: AuditService,
    ):
        self.plans_repo = plans_repo
        self.plan_service = plan_service
        self.ledger_service = ledger_service
        self.event_service = event_service
        self.audit_service = audit_service

    async def apply_decision(
        self,
        plan_id: str,
        req: PlanDecisionRequest,
        officer_id: str,
    ) -> PlanDecisionResponse:
        """
        Apply human decision (approved, edited, rejected) per CONTRACT §5.8 and MEMBER_B.md F10.
        """
        # 1. Fetch plan
        plan = self.plan_service.get_plan(plan_id)
        if not plan:
            raise APIError(
                code="PLAN_NOT_FOUND",
                message=f"Plan '{plan_id}' not found.",
                status_code=404,
            )

        # 2. Decision-state protection: only recommended plans may receive a decision
        if plan.status != "recommended":
            raise APIError(
                code="PLAN_ALREADY_DECIDED",
                message=f"Plan '{plan_id}' already has status '{plan.status}' and cannot receive another decision.",
                status_code=409,
                details={"current_status": plan.status},
            )

        now = datetime.now(timezone.utc)
        decision_type = req.decision

        if decision_type == "rejected":
            # Rejection requires a non-empty note
            if not req.note or not req.note.strip():
                raise APIError(
                    code="VALIDATION_ERROR",
                    message="A rejection note is required.",
                    status_code=422,
                    details={"field": "note"},
                )

            plan.status = "rejected"
            plan.decision = DecisionInfo(
                decision="rejected",
                officer_id=officer_id,
                note=req.note.strip(),
                decided_at=now,
                next_plan_id=None,
            )
            self.plans_repo.save(plan.model_dump(mode="json"))

            # Audit and event
            self.audit_service.record(
                officer_id=officer_id,
                action="plan_rejected",
                plan_id=plan.plan_id,
                scenario_id=plan.scenario_id,
                details={"note": req.note.strip()},
            )
            self.event_service.emit(
                name="plan_decision",
                scenario_id=plan.scenario_id,
                officer_id=officer_id,
                props={
                    "plan_id": plan.plan_id,
                    "decision": "rejected",
                    "officer_id": officer_id,
                },
            )

            return PlanDecisionResponse(
                plan=plan,
                next_plan=None,
                ledger_updated=False,
            )

        elif decision_type == "edited":
            # Edit requires revised weights
            if req.revised_weights is None:
                raise APIError(
                    code="VALIDATION_ERROR",
                    message="Revised weights are required when editing a plan.",
                    status_code=422,
                    details={"field": "revised_weights"},
                )

            # Generate new superseding plan synchronously
            new_plan = await self.plan_service.generate_plan(
                scenario_id=plan.scenario_id,
                request_weights=req.revised_weights,
                officer_id=officer_id,
                supersedes_plan_id=plan.plan_id,
            )

            # Update original plan status and decision
            plan.status = "edited"
            plan.decision = DecisionInfo(
                decision="edited",
                officer_id=officer_id,
                note=req.note,
                decided_at=now,
                next_plan_id=new_plan.plan_id,
            )
            self.plans_repo.save(plan.model_dump(mode="json"))

            # Audit and event
            self.audit_service.record(
                officer_id=officer_id,
                action="plan_edited",
                plan_id=plan.plan_id,
                scenario_id=plan.scenario_id,
                details={
                    "note": req.note,
                    "next_plan_id": new_plan.plan_id,
                    "revised_weights": req.revised_weights.model_dump(),
                },
            )
            self.event_service.emit(
                name="plan_decision",
                scenario_id=plan.scenario_id,
                officer_id=officer_id,
                props={
                    "plan_id": plan.plan_id,
                    "decision": "edited",
                    "officer_id": officer_id,
                },
            )

            return PlanDecisionResponse(
                plan=plan,
                next_plan=new_plan,
                ledger_updated=False,
            )

        elif decision_type == "approved":
            plan.status = "approved"
            plan.decision = DecisionInfo(
                decision="approved",
                officer_id=officer_id,
                note=req.note,
                decided_at=now,
                next_plan_id=None,
            )
            self.plans_repo.save(plan.model_dump(mode="json"))

            # Update deprivation-debt ledger
            self.ledger_service.update_on_approval(
                plan_data=plan.model_dump(),
                scenario_data=None,
                decay=settings.b_debt_decay,
                cap_litres=settings.b_debt_cap_litres,
            )

            # Audit and event
            self.audit_service.record(
                officer_id=officer_id,
                action="plan_approved",
                plan_id=plan.plan_id,
                scenario_id=plan.scenario_id,
                details={"note": req.note},
            )
            self.event_service.emit(
                name="plan_decision",
                scenario_id=plan.scenario_id,
                officer_id=officer_id,
                props={
                    "plan_id": plan.plan_id,
                    "decision": "approved",
                    "officer_id": officer_id,
                },
            )

            return PlanDecisionResponse(
                plan=plan,
                next_plan=None,
                ledger_updated=True,
            )

        else:
            raise APIError(
                code="VALIDATION_ERROR",
                message=f"Unknown decision '{decision_type}'.",
                status_code=422,
            )
