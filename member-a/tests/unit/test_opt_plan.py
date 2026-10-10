"""M6/M7: full PlanResult assembly (services/plan_service.build_plan) on perturbed and random scenarios."""
from __future__ import annotations

import time

import pytest

from jp_a.api.errors import SolverError
from jp_a.domain.entities import PlanResult, PredictionSet, Scenario, Weights
from jp_a.optimization import solve as solve_module
from jp_a.services.plan_service import build_plan

from .opt_helpers import assert_plan_invariants, golden_inputs, make_case, scenario_dict, stable_dump

ALLOWED_STATUSES = {"optimal", "feasible", "time_limited", "heuristic"}


def _with(scenario: Scenario, **updates) -> Scenario:
    data = scenario_dict(scenario)
    data.update(updates)
    return Scenario.model_validate(data)


def _tankers(scenario: Scenario, **fields) -> list[dict]:
    return [{**t.model_dump(mode="json"), **fields} for t in scenario.tankers]


def _alloc(plan: PlanResult, area_id: str):
    return next(a for a in plan.allocations if a.area_id == area_id)


def _codes(plan: PlanResult, area_id: str) -> list[str]:
    return [r.code for r in _alloc(plan, area_id).reasons]


def _solve(scenario, prediction, weights, debt, time_limit_s=8.0) -> PlanResult:
    plan = build_plan(scenario, prediction, weights, debt, time_limit_s)
    assert_plan_invariants(plan, scenario, prediction, weights, debt)
    return plan


# --------------------------------------------------------------------------- weights
def test_debt_weight_moves_water_to_the_indebted_area():
    scenario, prediction, _, _ = golden_inputs()
    debt = {"ar_01": 12_000}
    low = _solve(scenario, prediction, Weights(debt=0.0), debt)
    high = _solve(scenario, prediction, Weights(debt=5.0), debt)
    assert [a.area_id for a in low.allocations][-1] == "ar_01"
    assert [a.area_id for a in high.allocations][0] == "ar_01"
    assert _alloc(high, "ar_01").priority_weight == round(1 + 14_000 / 54_000 + 5.0 + 0.5 * 0.3, 4)
    assert _alloc(high, "ar_01").allocated_litres > _alloc(low, "ar_01").allocated_litres
    assert _alloc(high, "ar_01").allocated_litres == 14_000
    assert "HIGH_DEBT" in _codes(high, "ar_01")


def test_higher_travel_weight_never_increases_depot_distance_of_stops():
    scenario, prediction, debt = make_case(21, n_areas=10, n_tankers=4, supply_ratio=0.9)
    depot = scenario.depot.depot_id

    def star_km(plan: PlanResult) -> float:
        return sum(scenario.distance_km[depot][s.area_id] for r in plan.routes for s in r.stops)

    free = _solve(scenario, prediction, Weights(travel=0.0), debt, 3.0)
    costly = _solve(scenario, prediction, Weights(travel=500.0), debt, 3.0)
    if {free.solver.status, costly.solver.status} == {"optimal"}:
        assert star_km(costly) <= star_km(free) + 1e-9
    assert costly.weights.travel == 500.0


# --------------------------------------------------------------------------- edge scenarios
def test_no_available_tanker():
    scenario, prediction, weights, debt = golden_inputs()
    grounded = _with(scenario, tankers=_tankers(scenario, status="unavailable"))
    plan = _solve(grounded, prediction, weights, debt)
    assert (plan.solver.engine, plan.solver.status) == ("ortools-cp-sat", "optimal")
    assert plan.totals.tanker_capacity_available_litres == 0
    assert plan.totals.allocated_litres == 0 and plan.totals.travel_km == 0.0
    assert {r.status for r in plan.routes} == {"unavailable"}
    assert {u.reason_code for u in plan.unserved} == {"TANKER_CAPACITY_LIMIT"}
    assert all("NOT_SERVED" in _codes(plan, a.area_id) for a in plan.allocations)
    assert all("no tanker is available this window" in a.reason_text for a in plan.allocations)


def test_zero_supply():
    scenario, prediction, weights, debt = golden_inputs()
    dry = _with(scenario, supply={"tanker_water_available_litres": 0})
    plan = _solve(dry, prediction, weights, debt)
    assert plan.totals.allocated_litres == 0
    assert [r.status for r in plan.routes] == ["idle", "idle", "unavailable"]
    assert [u.reason_code for u in plan.unserved] == ["SUPPLY_EXHAUSTED"] * 3
    assert _alloc(plan, "ar_03").reasons[-1].params == {"supply_available_litres": 0, "unmet_litres": 6_000}


def test_area_without_shortfall():
    scenario, prediction, weights, debt = golden_inputs()
    areas = [
        a.model_copy(update={"predicted_need_litres": 23_000, "predicted_shortfall_litres": 0})
        if a.area_id == "ar_02"
        else a
        for a in prediction.areas
    ]
    covered = prediction.model_copy(update={"areas": areas})
    plan = _solve(scenario, covered, weights, debt)
    ar_02 = _alloc(plan, "ar_02")
    assert (ar_02.allocated_litres, ar_02.unmet_litres, ar_02.coverage_ratio, ar_02.urgency) == (0, 0, 1.0, 0.0)
    assert ar_02.deliveries == [] and not ar_02.is_unserved_remainder
    assert _codes(plan, "ar_02")[-1] == "NO_SHORTFALL"
    assert ar_02.reason_text.startswith("No tanker water needed")
    assert "ar_02" not in {u.area_id for u in plan.unserved}
    # The freed water goes to the other areas: 6,000 + 14,000 = 20,000 L, all of the supply.
    assert plan.totals.allocated_litres == 20_000 and plan.totals.unmet_litres == 0


def test_area_beyond_every_route_limit():
    scenario, prediction, weights, debt = golden_inputs()
    short = _with(scenario, tankers=_tankers(scenario, max_route_km=20.0))  # ar_03 round trip is 23.6 km
    plan = _solve(short, prediction, weights, debt)
    ar_03 = _alloc(plan, "ar_03")
    assert ar_03.allocated_litres == 0 and ar_03.rank == 1
    assert _codes(plan, "ar_03")[-1] == "ROUTE_LIMIT"
    assert ar_03.reasons[-1].params == {"distance_from_depot_km": 11.8}
    assert {"area_id": "ar_03", "unmet_litres": 6_000, "reason_code": "ROUTE_LIMIT"} in [
        u.model_dump() for u in plan.unserved
    ]
    assert plan.totals.allocated_litres == 20_000  # the water still reaches the reachable areas


def test_travel_cost_tradeoff_leaves_a_small_far_shortfall_unserved():
    scenario, prediction, weights, debt = golden_inputs()
    roomy = _with(
        scenario,
        tankers=_tankers(scenario, status="available"),
        supply={"tanker_water_available_litres": 40_000},
    )
    areas = [
        a.model_copy(update={"expected_piped_litres": 20_000, "predicted_shortfall_litres": 1_000})
        if a.area_id == "ar_03"
        else a
        for a in prediction.areas
    ]
    small_far = prediction.model_copy(update={"areas": areas})
    expensive = Weights(travel=500.0)
    plan = _solve(roomy, small_far, expensive, debt)
    ar_03 = _alloc(plan, "ar_03")
    assert ar_03.allocated_litres == 0
    assert _codes(plan, "ar_03")[-2:] == ["NOT_SERVED", "TRAVEL_COST_TRADEOFF"]
    assert ar_03.reasons[-1].params == {"distance_from_depot_km": 11.8, "travel_weight": 500.0}
    assert "costs more travel (travel weight 500)" in ar_03.reason_text
    # With the default travel weight the same stop is worth making.
    assert _alloc(_solve(roomy, small_far, weights, debt), "ar_03").allocated_litres == 1_000


def test_sub_unit_remainder_is_reported_not_hidden():
    scenario, prediction, weights, debt = golden_inputs()
    areas = [
        a.model_copy(update={"predicted_need_litres": 21_050, "predicted_shortfall_litres": 6_050})
        if a.area_id == "ar_03"
        else a
        for a in prediction.areas
    ]
    odd = prediction.model_copy(update={"areas": areas})
    plan = _solve(scenario, odd, weights, debt)
    ar_03 = _alloc(plan, "ar_03")
    assert (ar_03.allocated_litres, ar_03.unmet_litres) == (6_000, 50)
    assert "whole 100 L units" in ar_03.reason_text
    assert {"area_id": "ar_03", "unmet_litres": 50, "reason_code": "LOWER_RELATIVE_PRIORITY"} in [
        u.model_dump() for u in plan.unserved
    ]


def test_non_metric_matrix_is_repaired_to_zero_violations():
    scenario, prediction, weights, debt = golden_inputs()
    data = scenario_dict(scenario)
    data["distance_km"]["ar_01"]["ar_03"] = data["distance_km"]["ar_03"]["ar_01"] = 50.0  # breaks the triangle rule
    detour = Scenario.model_validate(data)
    plan = _solve(detour, prediction, weights, debt)
    assert plan.solver.status == "feasible"  # optimal allocation, but a stop had to be cut
    assert all(r.route_km <= r.max_route_km for r in plan.routes)
    assert _alloc(plan, "ar_01").allocated_litres == 0
    assert _codes(plan, "ar_01")[-1] == "ROUTE_LIMIT"


def test_unknown_debt_area_is_rejected():
    scenario, prediction, weights, _ = golden_inputs()
    with pytest.raises(ValueError, match="unknown area ids"):
        build_plan(scenario, prediction, weights, {"ar_42": 100})


# --------------------------------------------------------------------------- solver paths
@pytest.mark.parametrize("failure", ["none", "raises"])
def test_greedy_fallback(monkeypatch, failure):
    def broken(*args, **kwargs):
        if failure == "raises":
            raise RuntimeError("solver crashed")
        return None

    monkeypatch.setattr(solve_module, "solve_cpsat", broken)
    scenario, prediction, weights, debt = golden_inputs()
    plan = _solve(scenario, prediction, weights, debt, 2.0)
    assert (plan.solver.engine, plan.solver.status, plan.solver.time_limit_s) == ("greedy-fallback", "heuristic", 2.0)
    assert {a.area_id: a.allocated_litres for a in plan.allocations} == {"ar_03": 6_000, "ar_02": 9_000, "ar_01": 5_000}
    for alloc in plan.allocations:
        assert alloc.reasons[-1].model_dump() == {"code": "TIME_LIMITED_SOLUTION", "params": {"time_limit_s": 2.0}}
        assert "Fallback plan" in alloc.reason_text


def test_solver_error_when_every_engine_fails(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(solve_module, "solve_cpsat", broken)
    monkeypatch.setattr(solve_module, "solve_greedy", broken)
    scenario, prediction, weights, debt = golden_inputs()
    with pytest.raises(SolverError) as info:
        build_plan(scenario, prediction, weights, debt, 1.0)
    assert info.value.code == "SOLVER_ERROR" and info.value.status_code == 500


def test_half_second_limit_returns_quickly_with_a_feasible_plan():
    scenario, prediction, debt = make_case(14, n_areas=10, n_tankers=4, supply_ratio=0.4)
    started = time.perf_counter()
    plan = _solve(scenario, prediction, Weights(), debt, 0.5)
    assert time.perf_counter() - started < 1.5
    assert plan.solver.status in ALLOWED_STATUSES and plan.solver.time_limit_s == 0.5
    if plan.solver.status in {"time_limited", "heuristic"}:
        assert all(a.reasons[-1].code == "TIME_LIMITED_SOLUTION" for a in plan.allocations)


def test_ten_area_solves_meet_the_time_targets():
    scenario, prediction, debt = make_case(14, n_areas=10, n_tankers=4, supply_ratio=0.4)
    started = time.perf_counter()
    _solve(scenario, prediction, Weights(), debt, 8.0)
    assert time.perf_counter() - started < 10.0
    started = time.perf_counter()
    plan = _solve(scenario, prediction, Weights(), debt, 3.0)
    assert time.perf_counter() - started < 4.0
    assert plan.solver.status in ALLOWED_STATUSES


# --------------------------------------------------------------------------- random scenarios and determinism
@pytest.mark.parametrize(
    "seed,kwargs",
    [
        (101, {}),
        (102, {"supply_ratio": 0.3}),
        (103, {"supply_ratio": 1.4}),
        (104, {"unavailable": 1}),
        (105, {"unavailable": 3}),
        (106, {"max_route_km": 15.0}),
        (107, {"n_areas": 6, "n_tankers": 2}),
        (108, {"n_areas": 8, "n_tankers": 4, "radius_km": 12.0, "max_route_km": 30.0}),
        (109, {"n_tankers": 1, "supply_ratio": 2.0}),
        (110, {"n_tankers": 6}),
    ],
)
def test_random_scenarios_satisfy_every_invariant(seed, kwargs):
    scenario, prediction, debt = make_case(seed, **{"n_areas": 10, "n_tankers": 4, **kwargs})
    started = time.perf_counter()
    plan = _solve(scenario, prediction, Weights(), debt, 3.0)
    assert time.perf_counter() - started < 4.0
    assert plan.solver.status in ALLOWED_STATUSES
    assert all(a.allocated_litres <= a.predicted_shortfall_litres for a in plan.allocations)
    assert plan.constraint_check.violations == []


@pytest.mark.parametrize(
    "case",
    ["contract", "random", "flat_priorities"],
)
def test_identical_inputs_give_identical_plans(case):
    if case == "contract":
        scenario, prediction, weights, debt = golden_inputs()
    else:
        scenario, prediction, debt = make_case(0 if case == "random" else 11, n_areas=10, n_tankers=4)
        weights = Weights() if case == "random" else Weights(urgency=0.0, debt=0.0, vulnerability=0.0, travel=0.0)
    plans = [build_plan(scenario, prediction, weights, debt, 8.0) for _ in range(5)]
    assert all(p.solver.status == "optimal" for p in plans)
    reference = stable_dump(plans[0])
    assert all(stable_dump(p) == reference for p in plans[1:])
    assert len({p.result_id for p in plans}) == 5


def test_prediction_block_is_copied_from_the_prediction_set():
    scenario, prediction, weights, debt = golden_inputs()
    derived = PredictionSet.model_validate({**prediction.model_dump(mode="json"), "run_mae_litres": None, "latency_ms": 7})
    plan = build_plan(scenario, derived, weights, debt, 8.0)
    assert plan.prediction.model_dump() == {"model_version": "xgb-sa-v1", "latency_ms": 7, "run_mae_litres": None}
