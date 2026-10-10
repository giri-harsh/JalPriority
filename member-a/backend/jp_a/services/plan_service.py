"""Plan service (route 6.6) and the pure PlanResult assembly. OWNER: Agent OPT.

INTERFACE (frozen by LEAD):

    PlanService(settings, scenario_repo, prediction_service)
        solve(request: SolveRequest) -> PlanResult
            # 404 ScenarioNotFoundError; unknown debt_by_area area ids -> ContractValidationError (422);
            # time_limit_s None -> settings.a_solver_time_limit_s; NEVER an error for a time limit.
        build_plan(scenario, prediction, weights, debt_by_area, time_limit_s) -> PlanResult
            # PURE (no repo / model access) - the golden test and property tests call this directly.

The module-level ``build_plan`` is the same pure function with explicit optimizer settings; the method form
reads ``A_COVERAGE_FLOOR`` / ``A_FLOOR_PENALTY`` from the service's settings.

Pipeline: F-2..F-5 priorities -> rank -> CP-SAT allocation (greedy fallback) -> routing -> reasons and
templated text -> totals, unserved list and an independently recomputed F-15 constraint check.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Mapping

from ..api.errors import ContractValidationError, SolverError
from ..domain.constraints import (
    DELIVERY_UNIT_LITRES,
    KM_DP,
    build_constraint_check,
    distance,
    litres_to_units,
    route_km,
)
from ..domain.entities import (
    Allocation,
    Delivery,
    PlanResult,
    PredictionRef,
    PredictionSet,
    Route,
    RouteStop,
    Scenario,
    SolverInfo,
    SolveRequest,
    Totals,
    Unserved,
    Weights,
    new_result_id,
    now_iso,
)
from ..domain.priority import RATIO_DP, AreaPriority, bottom_third_cutoff, compute_area_priorities, rank_areas
from ..domain.reasons import AreaFacts, PlanFacts, TankerFacts
from ..logging_setup import log_event
from ..optimization.allocation import AllocationProblem, build_problem
from ..optimization.routing import build_path
from ..optimization.solve import DispatchSolution, solve_dispatch
from .explanation_service import explain_plan

STRATEGY = "jalpriority"
DEFAULT_TIME_LIMIT_S = 8.0


class PlanService:
    def __init__(self, settings, scenario_repo, prediction_service):
        self.settings = settings
        self.scenario_repo = scenario_repo
        self.prediction_service = prediction_service

    def solve(self, request: SolveRequest) -> PlanResult:
        started = time.perf_counter()
        scenario = self.scenario_repo.get(request.scenario_id)  # ScenarioNotFoundError -> 404
        known = {a.area_id for a in scenario.areas}
        unknown = sorted(set(request.debt_by_area) - known)
        if unknown:
            raise ContractValidationError(
                f"debt_by_area has area ids that are not in scenario {scenario.scenario_id}: {', '.join(unknown)}",
                details={"unknown_area_ids": unknown},
            )
        time_limit_s = (
            request.time_limit_s if request.time_limit_s is not None else self.settings.a_solver_time_limit_s
        )
        prediction = self.prediction_service.predict_for_scenario(scenario)  # ModelUnavailableError -> 503
        result = build_plan(
            scenario,
            prediction,
            request.weights,
            request.debt_by_area,
            time_limit_s,
            coverage_floor=self.settings.a_coverage_floor,
            floor_penalty=self.settings.a_floor_penalty,
            elapsed_s=time.perf_counter() - started,
        )
        log_event(
            "plan_solved",
            f"solved {scenario.scenario_id}",
            duration_ms=int(round((time.perf_counter() - started) * 1000)),
            engine=result.solver.engine,
            solver_status=result.solver.status,
            solve_time_ms=result.solver.solve_time_ms,
            allocated_litres=result.totals.allocated_litres,
            unmet_litres=result.totals.unmet_litres,
        )
        return result

    def build_plan(
        self,
        scenario: Scenario,
        prediction: PredictionSet,
        weights: Weights,
        debt_by_area: Mapping[str, int],
        time_limit_s: float | None = None,
    ) -> PlanResult:
        return build_plan(
            scenario,
            prediction,
            weights,
            debt_by_area,
            time_limit_s if time_limit_s is not None else self.settings.a_solver_time_limit_s,
            coverage_floor=self.settings.a_coverage_floor,
            floor_penalty=self.settings.a_floor_penalty,
        )


def build_plan(
    scenario: Scenario,
    prediction: PredictionSet,
    weights: Weights,
    debt_by_area: Mapping[str, int],
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    *,
    coverage_floor: float = 0.25,
    floor_penalty: float = 3.0,
    elapsed_s: float = 0.0,
) -> PlanResult:
    """Assemble the full CONTRACT 5.6 PlanResult for one scenario window. Pure apart from the clock.

    ``elapsed_s`` is request time already spent upstream (scenario load, prediction); it is taken out of the
    solver budget so the response stays within ``time_limit_s + 1.0`` seconds.
    Raises ``SolverError`` only if neither CP-SAT nor the greedy fallback can produce a plan.
    """
    started = time.perf_counter()
    records = compute_area_priorities(scenario, prediction, weights, debt_by_area)
    ranked = rank_areas(records)
    rank = {rec.area_id: idx + 1 for idx, rec in enumerate(ranked)}
    problem = build_problem(ranked, scenario, weights, coverage_floor=coverage_floor, floor_penalty=floor_penalty)
    try:
        dispatch = solve_dispatch(problem, scenario, rank, time_limit_s, elapsed_s=elapsed_s)
    except Exception as exc:  # noqa: BLE001 - every optimizer crash surfaces as SOLVER_ERROR
        log_event("solver_error", f"optimizer failed for {scenario.scenario_id}: {exc!r}", level=logging.ERROR)
        raise SolverError(
            "The optimizer failed to produce a plan.", details={"scenario_id": scenario.scenario_id}
        ) from exc

    routes = _routes(scenario, dispatch)
    delivered = dispatch.delivered_litres()
    deliveries = _deliveries_by_area(dispatch)
    area_facts = [_area_facts(rec, rank[rec.area_id], delivered, scenario, problem) for rec in ranked]
    plan_facts = _plan_facts(scenario, problem, dispatch, records, weights, time_limit_s)
    explanations = explain_plan(area_facts, plan_facts)

    allocations = []
    for rec, facts in zip(ranked, area_facts, strict=True):
        explanation = explanations[rec.area_id]
        allocations.append(
            Allocation(
                area_id=rec.area_id,
                rank=facts.rank,
                predicted_shortfall_litres=rec.shortfall_litres,
                allocated_litres=facts.allocated_litres,
                unmet_litres=facts.unmet_litres,
                coverage_ratio=_coverage(facts.allocated_litres, rec.shortfall_litres),
                priority_weight=rec.priority_weight,
                urgency=rec.urgency,
                debt_litres_used=rec.debt_litres,
                debt_normalized=rec.debt_normalized,
                vulnerability_used=rec.vulnerability,
                deliveries=deliveries.get(rec.area_id, []),
                reasons=explanation.reasons,
                reason_text=explanation.reason_text,
                is_unserved_remainder=facts.unmet_litres > 0,
            )
        )
    unserved = [
        Unserved(area_id=a.area_id, unmet_litres=a.unmet_litres, reason_code=explanations[a.area_id].unserved_reason_code)
        for a in allocations
        if a.unmet_litres > 0
    ]
    totals = Totals(
        supply_available_litres=scenario.supply.tanker_water_available_litres,
        tanker_capacity_available_litres=_available_capacity_litres(scenario),
        shortfall_litres=sum(rec.shortfall_litres for rec in records),
        allocated_litres=sum(a.allocated_litres for a in allocations),
        unmet_litres=sum(a.unmet_litres for a in allocations),
        travel_km=round(sum(r.route_km for r in routes), KM_DP),
    )
    constraint_check = build_constraint_check(scenario, allocations, routes)
    solve_time_ms = int(round((time.perf_counter() - started) * 1000))
    return PlanResult(
        result_id=new_result_id(),
        strategy=STRATEGY,
        scenario_id=scenario.scenario_id,
        service_area_id=scenario.service_area_id,
        window_index=scenario.window_index,
        generated_at=now_iso(),
        weights=Weights.model_validate(weights.model_dump()),
        solver=SolverInfo(
            engine=dispatch.engine, status=dispatch.status, solve_time_ms=solve_time_ms, time_limit_s=time_limit_s
        ),
        prediction=PredictionRef(
            model_version=prediction.model_version,
            latency_ms=prediction.latency_ms,
            run_mae_litres=prediction.run_mae_litres,
        ),
        totals=totals,
        allocations=allocations,
        routes=routes,
        unserved=unserved,
        constraint_check=constraint_check,
    )


# --------------------------------------------------------------------------- assembly helpers
def _coverage(allocated: int, shortfall: int) -> float:
    """F-7."""
    if shortfall == 0:
        return 1.0
    return round(min(1.0, allocated / shortfall), RATIO_DP)


def _available_capacity_litres(scenario: Scenario) -> int:
    return sum(t.capacity_litres for t in scenario.tankers if t.status == "available")


def _routes(scenario: Scenario, dispatch: DispatchSolution) -> list[Route]:
    """One route per scenario tanker, sorted by tanker_id: assigned / idle / unavailable."""
    depot_id = scenario.depot.depot_id
    routes = []
    for tanker in sorted(scenario.tankers, key=lambda t: t.tanker_id):
        trip = dispatch.trips.get(tanker.tanker_id)
        if tanker.status != "available" or trip is None:
            status = "unavailable" if tanker.status != "available" else "idle"
            stops: list[RouteStop] = []
            path: list[str] = []
        else:
            status = "assigned"
            stops = [
                RouteStop(stop_order=n, area_id=area_id, litres=litres)
                for n, (area_id, litres) in enumerate(trip.stops, start=1)
            ]
            path = build_path(depot_id, trip.area_ids)
        routes.append(
            Route(
                tanker_id=tanker.tanker_id,
                status=status,
                capacity_litres=tanker.capacity_litres,
                max_route_km=tanker.max_route_km,
                load_litres=sum(s.litres for s in stops),
                route_km=route_km(path, scenario.distance_km),
                stops=stops,
                path=path,
            )
        )
    return routes


def _deliveries_by_area(dispatch: DispatchSolution) -> dict[str, list[Delivery]]:
    """Each area's deliveries, with the stop order of its tanker's route, sorted by tanker_id."""
    by_area: dict[str, list[Delivery]] = defaultdict(list)
    for tanker_id in sorted(dispatch.trips):
        for n, (area_id, litres) in enumerate(dispatch.trips[tanker_id].stops, start=1):
            by_area[area_id].append(Delivery(tanker_id=tanker_id, stop_order=n, litres=litres))
    return dict(by_area)


def _area_facts(
    rec: AreaPriority, rank: int, delivered: Mapping[str, int], scenario: Scenario, problem: AllocationProblem
) -> AreaFacts:
    area = next(a for a in scenario.areas if a.area_id == rec.area_id)
    area_input = next(a for a in problem.areas if a.area_id == rec.area_id)
    return AreaFacts(
        area_id=rec.area_id,
        rank=rank,
        priority_weight=rec.priority_weight,
        urgency=rec.urgency,
        debt_litres=rec.debt_litres,
        debt_normalized=rec.debt_normalized,
        vulnerability=rec.vulnerability,
        vulnerability_available=area.vulnerability_available,
        heat_exposure=area.heat_exposure,
        predicted_need_litres=rec.predicted_need_litres,
        expected_piped_litres=rec.expected_piped_litres,
        shortfall_litres=rec.shortfall_litres,
        deliverable_litres=litres_to_units(rec.shortfall_litres) * DELIVERY_UNIT_LITRES,
        allocated_litres=delivered.get(rec.area_id, 0),
        depot_distance_km=distance(scenario.distance_km, scenario.depot.depot_id, rec.area_id),
        depot_metres=area_input.depot_metres,
    )


def _plan_facts(
    scenario: Scenario,
    problem: AllocationProblem,
    dispatch: DispatchSolution,
    records: list[AreaPriority],
    weights: Weights,
    time_limit_s: float,
) -> PlanFacts:
    depot_metres = {a.area_id: a.depot_metres for a in problem.areas}
    tankers = []
    for tanker in problem.tankers:
        trip = dispatch.trips.get(tanker.tanker_id)
        visits = frozenset(trip.area_ids) if trip else frozenset()
        load_units = trip.load_litres // DELIVERY_UNIT_LITRES if trip else 0
        tankers.append(
            TankerFacts(
                tanker_id=tanker.tanker_id,
                capacity_units=tanker.capacity_units,
                spare_units=tanker.capacity_units - load_units,
                route_limit_metres=tanker.route_limit_metres,
                route_used_metres=sum(2 * depot_metres[a] for a in visits),
                visits=visits,
            )
        )
    priorities = [rec.priority_weight for rec in records]
    return PlanFacts(
        supply_litres=scenario.supply.tanker_water_available_litres,
        supply_units=problem.supply_units,
        tanker_capacity_litres=_available_capacity_litres(scenario),
        capacity_units=sum(t.capacity_units for t in problem.tankers),
        allocated_units=sum(dispatch.delivered_litres().values()) // DELIVERY_UNIT_LITRES,
        tankers=tuple(tankers),
        travel_weight=weights.travel,
        alert_level=scenario.heat.alert_level,
        max_debt_litres=max((rec.debt_litres for rec in records), default=0),
        top_priority=max(priorities, default=0.0),
        bottom_third_cutoff=bottom_third_cutoff(priorities),
        n_areas=len(records),
        solver_status=dispatch.status,
        time_limit_s=time_limit_s,
    )
