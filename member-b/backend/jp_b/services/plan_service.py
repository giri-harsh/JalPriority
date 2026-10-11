import secrets
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from jp_b.api.schemas import (
    Weights,
    Scenario,
    PlanRecord,
    SolveRequest,
    Disruption,
)
from jp_b.config import settings
from jp_b.clients.a_client import AClient
from jp_b.repositories.base import PlansRepository, WeightsRepository
from jp_b.services.ledger_service import LedgerService
from jp_b.services.event_service import EventService
from jp_b.services.audit_service import AuditService
from jp_b.domain.metrics import compute_metrics

DEFAULT_WEIGHTS = Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)


class PlanService:
    def __init__(
        self,
        a_client: AClient,
        plans_repo: PlansRepository,
        ledger_service: LedgerService,
        weights_repo: WeightsRepository,
        event_service: EventService,
        audit_service: AuditService,
    ):
        self.a_client = a_client
        self.plans_repo = plans_repo
        self.ledger_service = ledger_service
        self.weights_repo = weights_repo
        self.event_service = event_service
        self.audit_service = audit_service

    def resolve_weights(
        self,
        service_area_id: str,
        request_weights: Optional[Weights] = None,
    ) -> Weights:
        """
        Step 1: Resolve weights and feature flags.
        Request weights -> stored weights -> defaults.
        B_FF_FAIRNESS_WEIGHTS=false -> ignore request weights and use defaults.
        B_FF_VULNERABILITY=false -> vulnerability = 0.
        """
        if not settings.b_ff_fairness_weights:
            resolved = DEFAULT_WEIGHTS.model_copy()
        elif request_weights is not None:
            resolved = request_weights.model_copy()
        else:
            stored = self.weights_repo.get(service_area_id)
            if stored and "weights" in stored:
                resolved = Weights.model_validate(stored["weights"])
            else:
                resolved = DEFAULT_WEIGHTS.model_copy()

        if not settings.b_ff_vulnerability:
            resolved.vulnerability = 0.0

        return resolved

    async def generate_plan(
        self,
        scenario_id: str,
        request_weights: Optional[Weights] = None,
        time_limit_s: Optional[float] = 8.0,
        officer_id: str = "officer_01",
        supersedes_plan_id: Optional[str] = None,
        replan_of_plan_id: Optional[str] = None,
        disruption: Optional[Disruption] = None,
    ) -> PlanRecord:
        """
        POST /b/v1/plans workflow per CONTRACT §5.8 and MEMBER_B.md B2.
        1. Resolve applicable weights and feature flags.
        2. Fetch the selected scenario from Member A.
        3. Seed and read the deprivation-debt ledger.
        4. Call Member A's solve endpoint.
        5. Compute the required metrics.
        6. Persist a PlanRecord with status recommended.
        7. Write a plan_generated audit entry.
        8. Emit the required prediction_generated and plan_generated events.
        """
        # Step 2: Fetch the selected scenario from Member A
        scenario = await self.a_client.get_scenario(scenario_id)
        scen_dict = scenario.model_dump()
        service_area_id = scenario.service_area_id

        # Step 1: Resolve weights and feature flags
        effective_weights = self.resolve_weights(
            service_area_id=service_area_id,
            request_weights=request_weights,
        )

        # Step 3: Seed and read the deprivation-debt ledger
        existing_ledger = self.ledger_service.ledger_repo.get_all_for_service_area(service_area_id)
        if not existing_ledger:
            self.ledger_service.seed_history(
                scen_dict,
                decay=settings.b_debt_decay,
                cap_litres=settings.b_debt_cap_litres,
            )

        all_debt = self.ledger_service.get_debt_by_area(
            service_area_id=service_area_id,
            window_index=scenario.window_index,
        )
        scenario_area_ids = {a.area_id for a in scenario.areas}
        debt_by_area = {aid: all_debt.get(aid, 0) for aid in scenario_area_ids}

        # Step 4: Call Member A's solve endpoint
        solve_request = SolveRequest(
            scenario_id=scenario_id,
            weights=effective_weights,
            debt_by_area=debt_by_area,
            time_limit_s=time_limit_s if time_limit_s is not None else 8.0,
        )
        plan_result = await self.a_client.solve(solve_request)

        # Step 5: Compute the required metrics
        metrics = compute_metrics(
            result=plan_result,
            debt_by_area=debt_by_area,
            decay=settings.b_debt_decay,
            cap_litres=settings.b_debt_cap_litres,
            replanning_time_ms=None,
        )

        # Step 6: Persist a PlanRecord with status recommended
        plan_id = f"pln_{secrets.token_hex(6)}"
        now = datetime.now(timezone.utc)
        plan_record = PlanRecord(
            schema_version="1.0",
            plan_id=plan_id,
            status="recommended",
            created_at=now,
            created_by=officer_id,
            scenario_id=scenario.scenario_id,
            service_area_id=scenario.service_area_id,
            window_index=scenario.window_index,
            supersedes_plan_id=supersedes_plan_id,
            replan_of_plan_id=replan_of_plan_id,
            disruption=disruption,
            is_synthetic=True,
            result=plan_result,
            metrics=metrics,
            decision=None,
        )
        self.plans_repo.save(plan_record.model_dump(mode="json"))

        # Step 7: Write a plan_generated audit entry
        self.audit_service.record(
            officer_id=officer_id,
            action="plan_generated",
            plan_id=plan_id,
            scenario_id=scenario.scenario_id,
            details={
                "status": "recommended",
                "strategy": "jalpriority",
                "weights": effective_weights.model_dump(),
            },
        )

        # Step 8: Emit prediction_generated and plan_generated events
        self.event_service.emit(
            name="prediction_generated",
            scenario_id=scenario.scenario_id,
            officer_id=officer_id,
            props={
                "model_version": plan_result.prediction.model_version,
                "run_mae_litres": plan_result.prediction.run_mae_litres,
                "latency_ms": plan_result.prediction.latency_ms,
            },
        )

        feasible = len(plan_result.constraint_check.violations) == 0
        self.event_service.emit(
            name="plan_generated",
            scenario_id=scenario.scenario_id,
            officer_id=officer_id,
            props={
                "plan_id": plan_id,
                "strategy": "jalpriority",
                "weights": effective_weights.model_dump(),
                "solve_time_ms": plan_result.solver.solve_time_ms,
                "solver_status": plan_result.solver.status,
                "feasible": feasible,
            },
        )

        return plan_record

    def get_plan(self, plan_id: str) -> Optional[PlanRecord]:
        data = self.plans_repo.get(plan_id)
        if not data:
            return None
        return PlanRecord.model_validate(data)

    def list_plans(
        self,
        scenario_id: Optional[str] = None,
        status: Optional[str] = None,
        latest: bool = False,
    ) -> List[PlanRecord]:
        rows = self.plans_repo.list_by_scenario(
            scenario_id=scenario_id,
            status=status,
            latest=latest,
        )
        return [PlanRecord.model_validate(r) for r in rows]
