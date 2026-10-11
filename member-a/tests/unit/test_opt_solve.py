"""M6/M7: dispatch orchestration (optimization/solve.py): time budget, engine selection and route repair."""
from __future__ import annotations

import pytest

from jp_a.domain.entities import Scenario
from jp_a.domain.priority import compute_area_priorities, rank_areas
from jp_a.optimization import solve as solve_module
from jp_a.optimization.allocation import (
    ENGINE_CP_SAT,
    ENGINE_GREEDY,
    AllocationOutcome,
    AllocationProblem,
    build_problem,
    objective_value,
    solve_greedy,
)
from jp_a.optimization.solve import OptimizationError, allocate, build_trips, solve_dispatch, solver_budget_s

from .opt_helpers import golden_inputs, scenario_dict


def _golden() -> tuple[Scenario, AllocationProblem, dict[str, int]]:
    scenario, prediction, weights, debt = golden_inputs()
    ranked = rank_areas(compute_area_priorities(scenario, prediction, weights, debt))
    problem = build_problem(ranked, scenario, weights, coverage_floor=0.25, floor_penalty=3.0)
    return scenario, problem, {rec.area_id: n for n, rec in enumerate(ranked, start=1)}


def _outcome(problem: AllocationProblem, units: dict, status: str) -> AllocationOutcome:
    return AllocationOutcome(engine=ENGINE_CP_SAT, status=status, units=units, objective=objective_value(problem, units))


def test_solver_budget():
    assert solver_budget_s(8.0) == 7.5
    assert solver_budget_s(8.0, elapsed_s=0.25) == 7.25
    assert solver_budget_s(0.5) == pytest.approx(0.1)
    assert solver_budget_s(0.5, elapsed_s=2.0) == pytest.approx(0.1)


def test_allocate_prefers_an_optimal_cp_sat_plan():
    _, problem, _ = _golden()
    outcome = allocate(problem, 8.0)
    assert (outcome.engine, outcome.status) == (ENGINE_CP_SAT, "optimal")


@pytest.mark.parametrize("behaviour", ["nothing", "raises"])
def test_allocate_falls_back_to_greedy(monkeypatch, behaviour):
    def cpsat(*args, **kwargs):
        if behaviour == "raises":
            raise RuntimeError("crash")
        return None

    monkeypatch.setattr(solve_module, "solve_cpsat", cpsat)
    _, problem, _ = _golden()
    outcome = allocate(problem, 1.0)
    assert (outcome.engine, outcome.status) == (ENGINE_GREEDY, "heuristic")


def test_time_limited_plan_worse_than_greedy_is_replaced(monkeypatch):
    _, problem, _ = _golden()
    worse = _outcome(problem, {("tk_01", "ar_01"): 10}, "time_limited")
    monkeypatch.setattr(solve_module, "solve_cpsat", lambda *a, **k: worse)
    outcome = allocate(problem, 1.0)
    assert outcome.engine == ENGINE_GREEDY
    assert outcome.objective == solve_greedy(problem).objective < worse.objective


def test_time_limited_plan_at_least_as_good_as_greedy_is_kept(monkeypatch):
    _, problem, _ = _golden()
    greedy_units = solve_greedy(problem).units
    tied = _outcome(problem, dict(greedy_units), "time_limited")
    monkeypatch.setattr(solve_module, "solve_cpsat", lambda *a, **k: tied)
    assert allocate(problem, 1.0) is tied


def test_cp_sat_still_runs_when_greedy_fails(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("greedy crash")

    monkeypatch.setattr(solve_module, "solve_greedy", broken)
    _, problem, _ = _golden()
    assert allocate(problem, 2.0).status == "optimal"
    monkeypatch.setattr(solve_module, "solve_cpsat", broken)
    with pytest.raises(OptimizationError):
        allocate(problem, 2.0)


def test_build_trips_sequences_and_converts_units():
    scenario, problem, rank = _golden()
    outcome = allocate(problem, 8.0)
    trips, dropped = build_trips(outcome, scenario, rank, routing_deadline=float("inf"))
    assert dropped == frozenset()
    assert {k: t.stops for k, t in trips.items()} == {
        "tk_01": (("ar_03", 6000), ("ar_01", 5000)),
        "tk_02": (("ar_02", 9000),),
    }
    assert trips["tk_01"].load_litres == 11_000 and trips["tk_01"].area_ids == ["ar_03", "ar_01"]


def test_route_repair_drops_the_lowest_priority_stop():
    scenario, problem, rank = _golden()
    data = scenario_dict(scenario)
    data["distance_km"]["ar_01"]["ar_03"] = data["distance_km"]["ar_03"]["ar_01"] = 50.0
    detour = Scenario.model_validate(data)
    dispatch = solve_dispatch(problem, detour, rank, 8.0)
    assert dispatch.dropped == frozenset({"ar_01"})
    assert dispatch.status == "feasible"
    assert dispatch.trips["tk_01"].stops == (("ar_03", 6000),)
    assert dispatch.delivered_litres() == {"ar_03": 6000, "ar_02": 9000}
