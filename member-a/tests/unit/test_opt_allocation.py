"""M6: CP-SAT allocation model, canonical (deterministic) optimum and the greedy fallback."""
from __future__ import annotations

import random
from collections import defaultdict

import pytest

from jp_a.domain.priority import compute_area_priorities, rank_areas
from jp_a.optimization.allocation import (
    ENGINE_CP_SAT,
    ENGINE_GREEDY,
    AllocationProblem,
    AreaInput,
    TankerInput,
    build_problem,
    objective_value,
    solve_cpsat,
    solve_greedy,
)

from .opt_helpers import golden_inputs, make_case


def _golden_problem(**overrides) -> AllocationProblem:
    scenario, prediction, weights, debt = golden_inputs()
    ranked = rank_areas(compute_area_priorities(scenario, prediction, weights, debt))
    return build_problem(ranked, scenario, weights, coverage_floor=0.25, floor_penalty=3.0, **overrides)


def _per_area(units: dict) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for (_, area_id), q in units.items():
        out[area_id] += q
    return dict(out)


def _assert_feasible(problem: AllocationProblem, units: dict) -> None:
    areas = {a.area_id: a for a in problem.areas}
    tankers = {t.tanker_id: t for t in problem.tankers}
    assert all(q > 0 for q in units.values())
    assert sum(units.values()) <= problem.supply_units
    for area_id, q in _per_area(units).items():
        assert q <= areas[area_id].shortfall_units
    load: dict[str, int] = defaultdict(int)
    star: dict[str, int] = defaultdict(int)
    for (tanker_id, area_id), q in units.items():
        load[tanker_id] += q
        star[tanker_id] += 2 * areas[area_id].depot_metres
    for tanker_id in load:
        assert load[tanker_id] <= tankers[tanker_id].capacity_units
        assert star[tanker_id] <= tankers[tanker_id].route_limit_metres


def _hand_problem(supply_units: int, floor_penalty: float, coverage_floor: float = 0.25) -> AllocationProblem:
    """Area A (p = 3.0, 10,000 L) and area B (p = 1.2, 4,000 L), one big tanker, no travel cost."""
    import math

    def area(area_id: str, p: float, sf: int) -> AreaInput:
        units = sf // 100
        cap = min(units, math.ceil(round((1 - coverage_floor) * sf / 100, 9)))
        coef = round(p * 100_000)
        return AreaInput(area_id, units, cap, coef, round(floor_penalty * coef), 0, 1000)

    return AllocationProblem(
        areas=(area("ar_01", 3.0, 10_000), area("ar_02", 1.2, 4_000)),
        tankers=(TankerInput("tk_01", 500, 60_000),),
        supply_units=supply_units,
    )


def test_build_problem_matches_the_formulation():
    problem = _golden_problem()
    assert [a.area_id for a in problem.areas] == ["ar_03", "ar_02", "ar_01"]  # rank order
    assert [a.shortfall_units for a in problem.areas] == [60, 90, 140]
    assert [a.normal_deficit_cap for a in problem.areas] == [45, 68, 105]  # ceil(0.75 * sf / 100)
    assert [a.priority_coef for a in problem.areas] == [268570, 221440, 140930]  # round(p * 1000 * 100)
    assert [a.floor_coef for a in problem.areas] == [805710, 664320, 422790]  # kappa = 3
    assert [a.travel_coef for a in problem.areas] == [236000, 128000, 62000]  # round(20 * d * 1000)
    assert [a.depot_metres for a in problem.areas] == [11800, 6400, 3100]
    assert [(t.tanker_id, t.capacity_units, t.route_limit_metres) for t in problem.tankers] == [
        ("tk_01", 120, 60000),
        ("tk_02", 100, 60000),
    ]  # tk_03 is unavailable and takes no part
    assert problem.supply_units == 200


def test_golden_allocation_is_optimal_and_unique():
    problem = _golden_problem()
    outcome = solve_cpsat(problem, 7.5)
    assert outcome is not None
    assert (outcome.engine, outcome.status) == (ENGINE_CP_SAT, "optimal")
    assert _per_area(outcome.units) == {"ar_03": 60, "ar_02": 90, "ar_01": 50}
    assert outcome.units == {("tk_01", "ar_01"): 50, ("tk_01", "ar_03"): 60, ("tk_02", "ar_02"): 90}
    assert outcome.objective == objective_value(problem, outcome.units) == 140930 * 90 + 236000 + 128000 + 62000


def test_greedy_is_feasible_and_never_better_than_cp_sat():
    problem = _golden_problem()
    greedy = solve_greedy(problem)
    assert (greedy.engine, greedy.status) == (ENGINE_GREEDY, "heuristic")
    _assert_feasible(problem, greedy.units)
    assert _per_area(greedy.units) == {"ar_03": 60, "ar_02": 90, "ar_01": 50}
    assert greedy.objective >= solve_cpsat(problem, 7.5).objective


def test_coverage_floor_protects_lower_priority_area():
    # kappa = 1: the floor term is inert and all 10,000 L go to the higher-priority area.
    flat = solve_cpsat(_hand_problem(supply_units=100, floor_penalty=1.0), 2.0)
    assert _per_area(flat.units) == {"ar_01": 100}
    # kappa = 3: B's units below its 25% floor cost 3 x 1.2 = 3.6 > 3.0, so B gets exactly its floor (1,000 L).
    floored = solve_cpsat(_hand_problem(supply_units=100, floor_penalty=3.0), 2.0)
    assert _per_area(floored.units) == {"ar_01": 90, "ar_02": 10}
    # The greedy fallback follows the same two-pass logic.
    assert _per_area(solve_greedy(_hand_problem(supply_units=100, floor_penalty=3.0)).units) == {
        "ar_01": 90,
        "ar_02": 10,
    }


def test_floor_penalty_below_one_is_scored_exactly():
    problem = _hand_problem(supply_units=100, floor_penalty=0.5)
    outcome = solve_cpsat(problem, 2.0)
    assert outcome.objective == objective_value(problem, outcome.units)
    _assert_feasible(problem, outcome.units)


def test_unreachable_pairs_get_no_water():
    problem = _golden_problem()
    short = AllocationProblem(
        areas=problem.areas,
        tankers=tuple(TankerInput(t.tanker_id, t.capacity_units, 20_000) for t in problem.tankers),  # 2 x 11.8 > 20
        supply_units=problem.supply_units,
    )
    outcome = solve_cpsat(short, 2.0)
    assert "ar_03" not in _per_area(outcome.units)
    _assert_feasible(short, outcome.units)
    _assert_feasible(short, solve_greedy(short).units)


def test_no_possible_delivery_returns_the_empty_optimal_plan():
    problem = _golden_problem()
    empty = AllocationProblem(areas=problem.areas, tankers=(), supply_units=200)
    outcome = solve_cpsat(empty, 1.0)
    assert (outcome.status, outcome.units) == ("optimal", {})
    assert solve_greedy(empty).units == {}


def test_zero_supply_allocates_nothing():
    problem = _golden_problem()
    dry = AllocationProblem(areas=problem.areas, tankers=problem.tankers, supply_units=0)
    assert solve_cpsat(dry, 1.0).units == {}
    assert solve_greedy(dry).units == {}


@pytest.mark.parametrize("seed", range(6))
def test_random_problems_are_feasible_and_cp_sat_beats_greedy(seed):
    scenario, prediction, debt = make_case(seed, n_areas=10, n_tankers=4, supply_ratio=random.Random(seed).uniform(0.3, 1.2))
    from jp_a.domain.entities import Weights

    ranked = rank_areas(compute_area_priorities(scenario, prediction, Weights(), debt))
    problem = build_problem(ranked, scenario, Weights(), coverage_floor=0.25, floor_penalty=3.0)
    greedy = solve_greedy(problem)
    outcome = solve_cpsat(problem, 7.5, hint=greedy.units)
    assert outcome.status == "optimal"
    _assert_feasible(problem, greedy.units)
    _assert_feasible(problem, outcome.units)
    assert outcome.objective <= greedy.objective


def test_identical_tankers_and_flat_priorities_give_a_canonical_plan():
    # Every area has p = 1 and travel is free: a huge set of equally good plans exists.
    scenario, prediction, debt = make_case(11, n_areas=10, n_tankers=4)
    tankers = [t.model_copy(update={"capacity_litres": 10_000}) for t in scenario.tankers]
    scenario = scenario.model_copy(update={"tankers": tankers})
    from jp_a.domain.entities import Weights

    flat = Weights(urgency=0.0, debt=0.0, vulnerability=0.0, travel=0.0)
    ranked = rank_areas(compute_area_priorities(scenario, prediction, flat, debt))
    problem = build_problem(ranked, scenario, flat, coverage_floor=0.25, floor_penalty=3.0)
    results = [solve_cpsat(problem, 7.5).units for _ in range(6)]
    assert all(r == results[0] for r in results)
    _assert_feasible(problem, results[0])


def test_tiny_time_limit_still_returns_a_feasible_plan_or_none():
    scenario, prediction, debt = make_case(3, n_areas=10, n_tankers=4)
    from jp_a.domain.entities import Weights

    ranked = rank_areas(compute_area_priorities(scenario, prediction, Weights(), debt))
    problem = build_problem(ranked, scenario, Weights(), coverage_floor=0.25, floor_penalty=3.0)
    outcome = solve_cpsat(problem, 0.1, hint=solve_greedy(problem).units)
    if outcome is not None:
        assert outcome.status in {"optimal", "feasible", "time_limited"}
        _assert_feasible(problem, outcome.units)
