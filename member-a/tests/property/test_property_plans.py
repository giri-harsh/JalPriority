"""M9 property tests (MEMBER_A section 7): 50 random scenarios through ``plan_service.build_plan``.

Scenarios are seeded (``random.Random``), so every run sees the same 50 cases. Each plan is checked by an
independent recomputation (``property_helpers.plan_problems``), not by the solver's own constraint check.
"""
from __future__ import annotations

import itertools
import time

import pytest

from jp_a.domain.entities import PlanResult, Weights
from jp_a.services.plan_service import build_plan

from .property_helpers import (
    ALLOWED_STATUSES,
    N_RANDOM,
    count_violations,
    plan_problems,
    random_case,
    stable_dump,
)

RANDOM_TIME_LIMIT_S = 2.0
HARD_CASE = {"n_areas": 10, "n_tankers": 4, "unavailable": 0, "supply_ratio": 0.55, "radius_km": 10.0, "max_route_km": 60.0}


@pytest.fixture(scope="module")
def solved_cases():
    """All 50 random plans, solved once and shared by the assertions below."""
    out = {}
    for seed in range(N_RANDOM):
        scenario, prediction, weights, debt = random_case(seed)
        started = time.perf_counter()
        plan = build_plan(scenario, prediction, weights, debt, RANDOM_TIME_LIMIT_S)
        out[seed] = (scenario, prediction, weights, debt, plan, time.perf_counter() - started)
    return out


# --------------------------------------------------------------------------- generator sanity
def test_random_cases_cover_the_required_dimensions():
    cases = [random_case(seed) for seed in range(N_RANDOM)]
    n_areas = {len(s.areas) for s, *_ in cases}
    n_tankers = {len(s.tankers) for s, *_ in cases}
    assert n_areas <= set(range(6, 11)) and len(n_areas) >= 4
    assert n_tankers <= {1, 2, 3, 4} and n_tankers == {1, 2, 3, 4}
    assert any(t.status == "unavailable" for s, *_ in cases for t in s.tankers)
    weights = [w for _, _, w, _ in cases]
    for name, top in (("urgency", 5.0), ("debt", 5.0), ("vulnerability", 5.0), ("travel", 500.0)):
        values = {getattr(w, name) for w in weights}
        assert 0.0 in values and top in values, f"{name} extremes missing"
    supplies = {s.supply.tanker_water_available_litres for s, *_ in cases}
    assert 0 in supplies and len(supplies) > 20
    assert len({tuple(sorted(d.items())) for *_, d in cases}) > 20


@pytest.mark.parametrize("seed", [0, 7, 23, 49])
def test_generated_distance_matrix_is_valid_and_symmetric(seed):
    scenario, *_ = random_case(seed)
    matrix = scenario.distance_km
    nodes = [scenario.depot.depot_id, *[a.area_id for a in scenario.areas]]
    assert set(matrix) == set(nodes)
    for a, b in itertools.product(nodes, nodes):
        assert matrix[a][b] == matrix[b][a]
        assert (matrix[a][b] == 0.0) == (a == b)
        assert matrix[a][b] >= 0.0


# --------------------------------------------------------------------------- 50 random scenarios
def test_all_random_plans_have_zero_violations(solved_cases):
    for seed, (scenario, _, _, _, plan, _) in solved_cases.items():
        assert count_violations(plan, scenario) == 0, f"seed {seed}"
        assert plan.constraint_check.violations == [], f"seed {seed}"


def test_all_random_plans_pass_every_independent_check(solved_cases):
    failures = {}
    for seed, (scenario, prediction, weights, debt, plan, _) in solved_cases.items():
        problems = plan_problems(plan, scenario, prediction, weights, debt)
        if problems:
            failures[seed] = problems[:5]
    assert not failures, failures


def test_allocation_never_exceeds_shortfall_supply_or_capacity(solved_cases):
    for seed, (scenario, prediction, _, _, plan, _) in solved_cases.items():
        shortfall = {p.area_id: max(0, p.predicted_need_litres - p.expected_piped_litres) for p in prediction.areas}
        capacity = {t.tanker_id: t.capacity_litres for t in scenario.tankers}
        # whole 100 L units, capped at the shortfall rounded UP; F-6 / F-7 absorb the (< 100 L) excess
        assert all(a.allocated_litres <= -(-shortfall[a.area_id] // 100) * 100 for a in plan.allocations), f"seed {seed}"
        assert all(a.unmet_litres == max(0, shortfall[a.area_id] - a.allocated_litres) for a in plan.allocations)
        assert all(
            a.coverage_ratio == (1.0 if shortfall[a.area_id] == 0 else round(min(1, a.allocated_litres / shortfall[a.area_id]), 4))
            for a in plan.allocations
        ), f"seed {seed}"
        assert sum(a.allocated_litres for a in plan.allocations) <= scenario.supply.tanker_water_available_litres
        assert all(r.load_litres <= capacity[r.tanker_id] for r in plan.routes), f"seed {seed}"


def test_rounded_up_units_serve_odd_shortfalls_in_full(solved_cases):
    """Random shortfalls are rarely multiples of 100 L; the ceil cap lets them be served in full (unmet 0)."""
    over = 0
    for seed, (_, prediction, _, _, plan, _) in solved_cases.items():
        shortfall = {p.area_id: p.predicted_shortfall_litres for p in prediction.areas}
        for a in plan.allocations:
            if a.allocated_litres > shortfall[a.area_id]:
                over += 1
                assert a.allocated_litres - shortfall[a.area_id] < 100, f"seed {seed} {a.area_id}"
                assert (a.unmet_litres, a.coverage_ratio, a.is_unserved_remainder) == (0, 1.0, False)
                codes = [r.code for r in a.reasons]
                assert "FULLY_SERVED" in codes and "LOWER_RELATIVE_PRIORITY" not in codes, f"seed {seed}"
        assert all(u.unmet_litres > 0 for u in plan.unserved), f"seed {seed}"
    assert over > 0, "no random plan exercised the rounded-up per-area cap"


def test_route_length_and_delivery_granularity(solved_cases):
    for seed, (scenario, _, _, _, plan, _) in solved_cases.items():
        limit = {t.tanker_id: t.max_route_km for t in scenario.tankers}
        for route in plan.routes:
            km = sum(scenario.distance_km[a][b] for a, b in zip(route.path, route.path[1:], strict=False))
            assert km <= limit[route.tanker_id] + 0.01, f"seed {seed} {route.tanker_id}"
        for alloc in plan.allocations:
            assert all(d.litres % 100 == 0 and d.litres > 0 for d in alloc.deliveries), f"seed {seed}"
            assert alloc.allocated_litres % 100 == 0


def test_unserved_list_is_consistent_with_unmet(solved_cases):
    for seed, (_, _, _, _, plan, _) in solved_cases.items():
        expected = [a.area_id for a in plan.allocations if a.unmet_litres > 0]
        assert [u.area_id for u in plan.unserved] == expected, f"seed {seed}"
        by_area = {a.area_id: a for a in plan.allocations}
        for u in plan.unserved:
            assert u.unmet_litres == by_area[u.area_id].unmet_litres > 0
            assert u.reason_code in {r.code for r in by_area[u.area_id].reasons}


def test_unavailable_tankers_get_no_stops(solved_cases):
    seen_unavailable = False
    for seed, (scenario, _, _, _, plan, _) in solved_cases.items():
        down = {t.tanker_id for t in scenario.tankers if t.status == "unavailable"}
        seen_unavailable |= bool(down)
        for route in plan.routes:
            if route.tanker_id in down:
                assert route.status == "unavailable" and route.stops == [] and route.load_litres == 0, f"seed {seed}"
        assert all(d.tanker_id not in down for a in plan.allocations for d in a.deliveries)
    assert seen_unavailable


def test_every_allocation_has_reasons_and_text(solved_cases):
    for seed, (_, _, _, _, plan, _) in solved_cases.items():
        for alloc in plan.allocations:
            assert alloc.reasons, f"seed {seed} {alloc.area_id}"
            assert alloc.reason_text.strip(), f"seed {seed} {alloc.area_id}"


def test_every_plan_parses_strictly(solved_cases):
    for seed, (_, _, _, _, plan, _) in solved_cases.items():
        PlanResult.model_validate_json(plan.model_dump_json())  # extra="forbid": no stray fields


def test_random_plans_finish_within_their_time_limit(solved_cases):
    for seed, (_, _, _, _, plan, elapsed) in solved_cases.items():
        assert plan.solver.status in ALLOWED_STATUSES
        assert elapsed < RANDOM_TIME_LIMIT_S + 1.0, f"seed {seed} took {elapsed:.2f}s"


# --------------------------------------------------------------------------- degenerate / extreme scenarios
@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("all_tankers_down", {"n_tankers": 3, "unavailable": 3}),
        ("zero_supply", {"supply_ratio": 0.0}),
        ("supply_far_above_shortfall", {"supply_ratio": 3.0}),
        ("single_tanker_tight_route", {"n_tankers": 1, "unavailable": 0, "max_route_km": 8.0, "radius_km": 14.0}),
        ("six_areas_one_tanker", {"n_areas": 6, "n_tankers": 1, "unavailable": 0}),
    ],
)
def test_degenerate_scenarios_stay_feasible(name, kwargs):
    scenario, prediction, weights, debt = random_case(321, **kwargs)
    plan = build_plan(scenario, prediction, weights, debt, 2.0)
    assert plan_problems(plan, scenario, prediction, weights, debt) == [], name
    if name in {"all_tankers_down", "zero_supply"}:
        assert plan.totals.allocated_litres == 0


WEIGHT_CORNERS = list(itertools.product((0.0, 5.0), (0.0, 5.0), (0.0, 5.0), (0.0, 500.0)))


@pytest.mark.parametrize("corner", WEIGHT_CORNERS, ids=lambda c: "u{}-d{}-v{}-t{}".format(*map(int, c)))
def test_extreme_weight_corners_keep_plans_feasible(corner):
    urgency, debt_w, vuln, travel = corner
    weights = Weights(urgency=urgency, debt=debt_w, vulnerability=vuln, travel=travel)
    scenario, prediction, weights, debt = random_case(77, weights=weights, **HARD_CASE)
    plan = build_plan(scenario, prediction, weights, debt, 2.0)
    assert plan_problems(plan, scenario, prediction, weights, debt) == []
    assert plan.weights == weights


# --------------------------------------------------------------------------- time-limit behaviour
def test_time_limit_half_second_is_respected():
    scenario, prediction, weights, debt = random_case(5, **HARD_CASE)
    started = time.perf_counter()
    plan = build_plan(scenario, prediction, weights, debt, 0.5)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5 + 0.5, f"0.5 s limit took {elapsed:.2f}s"  # contract: <= limit + 1.0, plus CI slack
    assert plan.solver.status in ALLOWED_STATUSES
    assert plan.solver.time_limit_s == 0.5
    assert plan_problems(plan, scenario, prediction, weights, debt) == []
    assert count_violations(plan, scenario) == 0


@pytest.mark.parametrize("seed", range(3))
def test_time_limit_half_second_on_several_hard_cases(seed):
    scenario, prediction, weights, debt = random_case(100 + seed, **HARD_CASE)
    started = time.perf_counter()
    plan = build_plan(scenario, prediction, weights, debt, 0.5)
    assert time.perf_counter() - started < 2.0
    assert plan.solver.status in ALLOWED_STATUSES
    assert count_violations(plan, scenario) == 0


def test_default_time_limit_finishes_under_ten_seconds():
    scenario, prediction, weights, debt = random_case(5, **HARD_CASE)
    started = time.perf_counter()
    plan = build_plan(scenario, prediction, weights, debt)  # DEFAULT_TIME_LIMIT_S
    elapsed = time.perf_counter() - started
    assert elapsed < 10.0, f"default limit took {elapsed:.2f}s"
    assert count_violations(plan, scenario) == 0
    assert plan.solver.status in ALLOWED_STATUSES


def test_three_second_limit_finishes_under_four_seconds():
    scenario, prediction, weights, debt = random_case(5, **HARD_CASE)
    started = time.perf_counter()
    plan = build_plan(scenario, prediction, weights, debt, 3.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 4.0, f"3.0 s limit took {elapsed:.2f}s"
    assert count_violations(plan, scenario) == 0
    assert plan.solver.time_limit_s == 3.0


def test_elapsed_upstream_time_is_taken_out_of_the_budget():
    scenario, prediction, weights, debt = random_case(5, **HARD_CASE)
    started = time.perf_counter()
    plan = build_plan(scenario, prediction, weights, debt, 1.0, elapsed_s=0.4)
    assert time.perf_counter() - started < 1.0 + 1.0
    assert count_violations(plan, scenario) == 0


# --------------------------------------------------------------------------- determinism
@pytest.mark.parametrize("seed", [3, 11, 29])
def test_repeated_solves_are_identical(seed):
    scenario, prediction, weights, debt = random_case(seed)
    plans = [build_plan(scenario, prediction, weights, debt, 3.0) for _ in range(3)]
    if any(p.solver.status != "optimal" for p in plans):
        pytest.skip("not solved to optimality; identical output is only guaranteed for optimal solves")
    first = stable_dump(plans[0])
    assert all(stable_dump(p) == first for p in plans[1:])


def test_input_order_of_debt_map_does_not_change_the_plan():
    scenario, prediction, weights, debt = random_case(13)
    reordered = dict(reversed(list(debt.items())))
    a = build_plan(scenario, prediction, weights, debt, 3.0)
    b = build_plan(scenario, prediction, weights, reordered, 3.0)
    if a.solver.status != "optimal" or b.solver.status != "optimal":
        pytest.skip("not solved to optimality")
    assert stable_dump(a) == stable_dump(b)


# --------------------------------------------------------------------------- priority monotonicity
def _raise_debt_candidates(scenario, debt):
    """(area, new_debt): raise a non-maximal debt up to the current maximum so the F-3 denominator is unchanged."""
    top = max(debt.values(), default=0)
    ids = [a.area_id for a in scenario.areas]
    return [(i, top) for i in ids if debt.get(i, 0) < top]


@pytest.mark.parametrize("seed", range(12))
def test_raising_an_areas_debt_never_lowers_its_allocation(seed):
    scenario, prediction, weights, debt = random_case(200 + seed, supply_ratio=0.5, unavailable=0)
    # the debt weight must matter for the property to say anything
    weights = Weights(urgency=weights.urgency, debt=max(weights.debt, 1.0), vulnerability=weights.vulnerability, travel=weights.travel)
    candidates = _raise_debt_candidates(scenario, debt)
    if not candidates:
        pytest.skip("no area with debt below the maximum")
    base = build_plan(scenario, prediction, weights, debt, 3.0)
    if base.solver.status != "optimal":
        pytest.skip("baseline not optimal")
    base_alloc = {a.area_id: a for a in base.allocations}
    checked = 0
    for area_id, new_debt in candidates[:3]:
        raised_debt = {**debt, area_id: new_debt}
        raised = build_plan(scenario, prediction, weights, raised_debt, 3.0)
        if raised.solver.status != "optimal":
            continue
        after = {a.area_id: a for a in raised.allocations}
        assert after[area_id].priority_weight >= base_alloc[area_id].priority_weight
        assert after[area_id].allocated_litres >= base_alloc[area_id].allocated_litres, (
            f"seed {seed}: {area_id} lost water when its debt rose to {new_debt}"
        )
        checked += 1
    assert checked >= 1


@pytest.mark.parametrize("seed", range(4))
def test_priority_weight_is_monotone_in_the_debt_weight(seed):
    scenario, prediction, _, debt = random_case(300 + seed, supply_ratio=0.5)
    low = build_plan(scenario, prediction, Weights(urgency=1, debt=0.5, vulnerability=0.5, travel=20), debt, 3.0)
    high = build_plan(scenario, prediction, Weights(urgency=1, debt=4.0, vulnerability=0.5, travel=20), debt, 3.0)
    p_low = {a.area_id: a.priority_weight for a in low.allocations}
    p_high = {a.area_id: a.priority_weight for a in high.allocations}
    assert all(p_high[i] >= p_low[i] for i in p_low)
