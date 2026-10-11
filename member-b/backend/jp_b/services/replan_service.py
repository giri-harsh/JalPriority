import time
import secrets
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from jp_b.api.schemas import (
    PlanRecord,
    Disruption,
    ReplanResponse,
    SolveRequest,
)
from jp_b.api.errors import APIError
from jp_b.config import settings
from jp_b.clients.a_client import AClient
from jp_b.repositories.base import PlansRepository
from jp_b.services.event_service import EventService
from jp_b.services.audit_service import AuditService
from jp_b.domain.metrics import compute_metrics
from jp_b.domain.diff import compute_plan_diff


class ReplanService:
    def __init__(
        self,
        a_client: AClient,
        plans_repo: PlansRepository,
        event_service: EventService,
        audit_service: AuditService,
    ):
        self.a_client = a_client
        self.plans_repo = plans_repo
        self.event_service = event_service
        self.audit_service = audit_service

    async def replan(
        self,
        plan_id: str,
        disruption: Disruption,
        officer_id: str = "officer_01",
    ) -> ReplanResponse:
        """
        POST /b/v1/replans workflow per CONTRACT §5.8, §5.12, §7 and MEMBER_B.md F8 (B8).
        """
        # Step 1: Validate feature flag
        if not settings.b_ff_replan:
            raise APIError(
                code="FEATURE_DISABLED",
                message="Disruption replanning is disabled by feature flag B_FF_REPLAN.",
                status_code=403,
            )

        # Step 2: Start timing
        start_time = time.perf_counter()

        # Step 3: Load previous plan
        prev_raw = self.plans_repo.get(plan_id)
        if not prev_raw:
            raise APIError(
                code="PLAN_NOT_FOUND",
                message=f"Plan '{plan_id}' not found.",
                status_code=404,
            )
        previous_plan = PlanRecord.model_validate(prev_raw)

        # Preserve previous plan's weights and debt inputs
        effective_weights = previous_plan.result.weights
        debt_by_area = {
            a.area_id: a.debt_litres_used for a in previous_plan.result.allocations
        }

        # Step 4: Apply disruption through Member A
        derived_scenario = await self.a_client.apply_disruption(
            scenario_id=previous_plan.scenario_id,
            disruption=disruption.model_dump(),
        )

        # Step 5: Solve on derived scenario
        solve_req = SolveRequest(
            scenario_id=derived_scenario.scenario_id,
            weights=effective_weights,
            debt_by_area=debt_by_area,
            time_limit_s=settings.b_replan_time_limit_s,
        )
        plan_result = await self.a_client.solve(solve_req)

        # Compute metrics
        metrics = compute_metrics(
            result=plan_result,
            debt_by_area=debt_by_area,
            decay=settings.b_debt_decay,
            cap_litres=settings.b_debt_cap_litres,
            replanning_time_ms=None,
        )

        new_plan_id = f"pln_{secrets.token_hex(6)}"
        now = datetime.now(timezone.utc)
        new_plan = PlanRecord(
            schema_version="1.0",
            plan_id=new_plan_id,
            status="recommended",
            created_at=now,
            created_by=officer_id,
            scenario_id=derived_scenario.scenario_id,
            service_area_id=derived_scenario.service_area_id,
            window_index=derived_scenario.window_index,
            supersedes_plan_id=None,
            replan_of_plan_id=previous_plan.plan_id,
            disruption=disruption,
            is_synthetic=True,
            result=plan_result,
            metrics=metrics,
            decision=None,
        )

        # Step 6: Compute before-and-after diff
        diff = compute_plan_diff(previous_plan, new_plan)

        # Step 7: Record elapsed time
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        new_plan.metrics.replanning_time_ms = elapsed_ms
        diff.summary.metrics_after.replanning_time_ms = elapsed_ms

        # Step 8: Update plan statuses
        if previous_plan.status == "recommended":
            previous_plan.status = "superseded"
            self.plans_repo.save(previous_plan.model_dump(mode="json"))

        self.plans_repo.save(new_plan.model_dump(mode="json"))

        # Step 9: Persist audit and events
        # Compute magnitude per CONTRACT §7
        if disruption.type == "tanker_down":
            magnitude = 1
        elif disruption.type == "supply_cut":
            magnitude = disruption.params.get("fraction", 0.0)
        elif disruption.type == "heat_alert":
            magnitude = disruption.params.get("alert_level", 0)
        else:
            magnitude = 1

        self.event_service.emit(
            name="disruption_applied",
            scenario_id=derived_scenario.scenario_id,
            officer_id=officer_id,
            props={
                "type": disruption.type,
                "magnitude": magnitude,
                "params": disruption.params,
                "derived_scenario_id": derived_scenario.scenario_id,
            },
        )

        self.event_service.emit(
            name="replan_completed",
            scenario_id=derived_scenario.scenario_id,
            officer_id=officer_id,
            props={
                "previous_plan_id": previous_plan.plan_id,
                "new_plan_id": new_plan.plan_id,
                "replanning_time_ms": elapsed_ms,
                "diff_summary": {
                    "areas_changed": diff.summary.areas_changed,
                    "litres_moved": diff.summary.litres_moved,
                },
            },
        )

        self.audit_service.record(
            officer_id=officer_id,
            action="replan_applied",
            plan_id=new_plan.plan_id,
            scenario_id=derived_scenario.scenario_id,
            details={
                "previous_plan_id": previous_plan.plan_id,
                "disruption": disruption.model_dump(),
                "replanning_time_ms": elapsed_ms,
            },
        )

        # Step 10: Return response
        return ReplanResponse(
            schema_version="1.0",
            previous_plan_id=previous_plan.plan_id,
            derived_scenario_id=derived_scenario.scenario_id,
            disruption=disruption,
            replanning_time_ms=elapsed_ms,
            new_plan=new_plan,
            diff=diff,
        )
