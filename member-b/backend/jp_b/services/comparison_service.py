from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from jp_b.api.schemas import (
    Weights,
    PlanResult,
    Metrics,
    ComparisonResponse,
)
from jp_b.config import settings
from jp_b.clients.a_client import AClient
from jp_b.services.plan_service import PlanService
from jp_b.services.baseline_service import solve_baseline
from jp_b.services.ledger_service import LedgerService
from jp_b.services.event_service import EventService
from jp_b.domain.metrics import compute_metrics


def _weights_match(w1: Weights, w2: Weights) -> bool:
    return (
        abs(w1.urgency - w2.urgency) < 1e-4
        and abs(w1.debt - w2.debt) < 1e-4
        and abs(w1.vulnerability - w2.vulnerability) < 1e-4
        and abs(w1.travel - w2.travel) < 1e-4
    )


def compute_best_by_metric(metrics_by_strategy: Dict[str, Metrics]) -> Dict[str, str]:
    """
    Compute best strategy per metric according to CONTRACT §5.7 and MEMBER_B.md B7.
    Lower is better for unmet, debt, travel, violations.
    Higher is better for min_coverage_ratio.
    Ties resolve to 'tie'.
    """
    metric_directions = {
        "priority_weighted_unmet": "min",
        "deprivation_debt_remaining_litres": "min",
        "total_unmet_litres": "min",
        "min_coverage_ratio": "max",
        "total_travel_km": "min",
        "constraint_violations": "min",
    }
    best_by_metric: Dict[str, str] = {}
    for metric_name, direction in metric_directions.items():
        values = {strat: getattr(m, metric_name) for strat, m in metrics_by_strategy.items()}
        if direction == "min":
            target_val = min(values.values())
        else:
            target_val = max(values.values())

        winners = [strat for strat, val in values.items() if abs(val - target_val) < 1e-4]
        if len(winners) == 1:
            best_by_metric[metric_name] = winners[0]
        else:
            best_by_metric[metric_name] = "tie"

    return best_by_metric


class ComparisonService:
    def __init__(
        self,
        a_client: AClient,
        plan_service: PlanService,
        ledger_service: LedgerService,
        event_service: EventService,
    ):
        self.a_client = a_client
        self.plan_service = plan_service
        self.ledger_service = ledger_service
        self.event_service = event_service

    async def run_comparison(
        self,
        scenario_id: str,
        request_weights: Optional[Weights] = None,
        officer_id: str = "officer_01",
    ) -> ComparisonResponse:
        """
        Produce comparison of 3 strategies (jalpriority, fcfs, nearest_first)
        per CONTRACT §5.6, §5.7 and MEMBER_B.md F7 (B5) & B7.
        """
        # 1. Fetch scenario from Member A
        scenario = await self.a_client.get_scenario(scenario_id)
        scen_dict = scenario.model_dump()
        service_area_id = scenario.service_area_id

        # 2. Resolve effective weights
        effective_weights = self.plan_service.resolve_weights(
            service_area_id=service_area_id,
            request_weights=request_weights,
        )

        # 3. Seed ledger if needed and obtain debt by area
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

        # 4. Obtain JalPriority result (reuse latest recommended/approved plan for same scenario and weights, or generate)
        existing_plans = self.plan_service.list_plans(scenario_id=scenario_id)
        matching_plan = None
        for p in existing_plans:
            if p.status in ["recommended", "approved"] and _weights_match(p.result.weights, effective_weights):
                matching_plan = p
                break

        if matching_plan:
            jalpriority_plan = matching_plan
        else:
            jalpriority_plan = await self.plan_service.generate_plan(
                scenario_id=scenario_id,
                request_weights=effective_weights,
                officer_id=officer_id,
            )

        jp_result = jalpriority_plan.result
        jp_metrics = compute_metrics(
            result=jp_result,
            debt_by_area=debt_by_area,
            decay=settings.b_debt_decay,
            cap_litres=settings.b_debt_cap_litres,
            replanning_time_ms=None,
        )
        plan_id = jalpriority_plan.plan_id

        # 5. Fetch predictions from Member A
        prediction = await self.a_client.get_predictions(scenario_id)

        # 6. Run baselines with identical scenario, weights, and debt inputs
        fcfs_result = solve_baseline(
            strategy="fcfs",
            scenario=scenario,
            prediction=prediction,
            weights=effective_weights,
            debt_by_area=debt_by_area,
        )
        fcfs_metrics = compute_metrics(
            result=fcfs_result,
            debt_by_area=debt_by_area,
            decay=settings.b_debt_decay,
            cap_litres=settings.b_debt_cap_litres,
            replanning_time_ms=None,
        )

        nf_result = solve_baseline(
            strategy="nearest_first",
            scenario=scenario,
            prediction=prediction,
            weights=effective_weights,
            debt_by_area=debt_by_area,
        )
        nf_metrics = compute_metrics(
            result=nf_result,
            debt_by_area=debt_by_area,
            decay=settings.b_debt_decay,
            cap_litres=settings.b_debt_cap_litres,
            replanning_time_ms=None,
        )

        # 7. Emit 2 plan_generated events for baselines (plan_id: null)
        fcfs_feasible = len(fcfs_result.constraint_check.violations) == 0
        self.event_service.emit(
            name="plan_generated",
            scenario_id=scenario_id,
            officer_id=officer_id,
            props={
                "plan_id": None,
                "strategy": "fcfs",
                "weights": effective_weights.model_dump(),
                "solve_time_ms": fcfs_result.solver.solve_time_ms,
                "solver_status": fcfs_result.solver.status,
                "feasible": fcfs_feasible,
            },
        )

        nf_feasible = len(nf_result.constraint_check.violations) == 0
        self.event_service.emit(
            name="plan_generated",
            scenario_id=scenario_id,
            officer_id=officer_id,
            props={
                "plan_id": None,
                "strategy": "nearest_first",
                "weights": effective_weights.model_dump(),
                "solve_time_ms": nf_result.solver.solve_time_ms,
                "solver_status": nf_result.solver.status,
                "feasible": nf_feasible,
            },
        )

        # 8. Compute best_by_metric
        metrics_by_strategy = {
            "jalpriority": jp_metrics,
            "fcfs": fcfs_metrics,
            "nearest_first": nf_metrics,
        }
        best_by_metric = compute_best_by_metric(metrics_by_strategy)

        now = datetime.now(timezone.utc)
        return ComparisonResponse(
            schema_version="1.0",
            scenario_id=scenario_id,
            weights=effective_weights,
            generated_at=now,
            is_simulation=True,
            plan_id=plan_id,
            results={
                "jalpriority": jp_result,
                "fcfs": fcfs_result,
                "nearest_first": nf_result,
            },
            metrics=metrics_by_strategy,
            best_by_metric=best_by_metric,
        )
