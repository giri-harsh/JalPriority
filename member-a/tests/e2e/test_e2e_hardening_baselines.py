"""JalPriority versus the two simple baselines, scored with the CONTRACT formulas on the REAL API's output.

Baselines (implemented here, independent of ``e2e_helpers``), both spending the SAME supply on the REQUESTED litres and
ignoring tanker routing:

* FCFS: areas in ``request_time`` order (ties by area id);
* nearest first: areas in depot road-distance order (ties by area id).

Scoring (CONTRACT 5.11):

* F-6  ``u_i = max(0, sf_i - allocated_i)`` (water above the predicted shortfall is waste, it does not reduce ``u_i``);
* F-9  ``round(sum p_i * u_i, 1)`` with the SAME ``p_i`` for every strategy, taken from JalPriority's solve result
  (default weights, debt from ``jp_a.data.debt`` over the scenario history, lambda = 0.3);
* F-13 ``min c_i`` over areas with ``sf_i > 0`` (``1.0`` if there are none), ``c_i = min(1, allocated_i / sf_i)``.

Assertions: on ``scn_sa01_w06`` and ``scn_sa01_w06_severe`` JalPriority has a strictly LOWER priority-weighted unmet and
a HIGHER-or-equal minimum coverage than both baselines. All three seeds are printed (``pytest -s``) as a table.
"""
from __future__ import annotations

import pytest

from jp_a.data.debt import debt_by_area
from jp_a.domain.entities import PlanResult, Scenario

from .e2e_helpers import MAIN_ID, MILD_ID, SEED_IDS, SEVERE_ID, solve_body

DEMO_IDS = (MAIN_ID, SEVERE_ID)
TIME_LIMIT_S = 8.0  # the production default


# --------------------------------------------------------------------------- baselines
def _serve(order: list[str], requested: dict[str, int], supply: int) -> dict[str, int]:
    left = supply
    out: dict[str, int] = {}
    for area_id in order:
        give = min(requested[area_id], left)
        out[area_id] = give
        left -= give
    return out


def fcfs(scenario: Scenario) -> dict[str, int]:
    order = [a.area_id for a in sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id))]
    return _serve(order, {a.area_id: a.requested_litres for a in scenario.areas}, scenario.supply.tanker_water_available_litres)


def nearest_first(scenario: Scenario) -> dict[str, int]:
    depot = scenario.depot.depot_id
    dist = {a.area_id: scenario.distance_km[depot][a.area_id] for a in scenario.areas}
    order = sorted(dist, key=lambda a: (dist[a], a))
    return _serve(order, {a.area_id: a.requested_litres for a in scenario.areas}, scenario.supply.tanker_water_available_litres)


# --------------------------------------------------------------------------- scoring (F-6, F-9, F-13)
def unmet(allocated: dict[str, int], sf: dict[str, int]) -> dict[str, int]:
    return {a: max(0, sf[a] - allocated.get(a, 0)) for a in sf}


def priority_weighted_unmet(allocated: dict[str, int], sf: dict[str, int], p: dict[str, float]) -> float:
    u = unmet(allocated, sf)
    return round(sum(p[a] * u[a] for a in sf), 1)


def min_coverage(allocated: dict[str, int], sf: dict[str, int]) -> float:
    cov = [round(min(1.0, allocated.get(a, 0) / sf[a]), 4) for a in sf if sf[a] > 0]
    return min(cov) if cov else 1.0


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def scored(client_a):
    """{scenario_id: {strategy: {"pwu", "unmet", "min_cov", "alloc"}}} for JalPriority, FCFS and nearest-first."""
    out: dict[str, dict] = {}
    for sid in SEED_IDS:
        scenario = client_a.scenario(sid)
        r = client_a.solve(solve_body(scenario, time_limit_s=TIME_LIMIT_S))
        assert r.status_code == 200, r.text
        plan = PlanResult.model_validate(r.json())
        sf = {a.area_id: a.predicted_shortfall_litres for a in plan.allocations}
        p = {a.area_id: a.priority_weight for a in plan.allocations}
        jp_alloc = {a.area_id: a.allocated_litres for a in plan.allocations}
        strategies = {"jalpriority": jp_alloc, "fcfs": fcfs(scenario), "nearest_first": nearest_first(scenario)}
        out[sid] = {
            "plan": plan,
            "scenario": scenario,
            "sf": sf,
            "p": p,
            **{
                name: {
                    "alloc": alloc,
                    "pwu": priority_weighted_unmet(alloc, sf, p),
                    "unmet": sum(unmet(alloc, sf).values()),
                    "min_cov": min_coverage(alloc, sf),
                }
                for name, alloc in strategies.items()
            },
        }
    return out


# --------------------------------------------------------------------------- sanity of the scoring itself
@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_priorities_come_from_jalpriority_with_default_weights_and_history_debt(client_a, scored, scenario_id):
    scenario = scored[scenario_id]["scenario"]
    plan = scored[scenario_id]["plan"]
    assert plan.weights.model_dump() == {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
    debt = debt_by_area(scenario)
    used = {a.area_id: a.debt_litres_used for a in plan.allocations}
    assert used == {area_id: debt[area_id] for area_id in used}
    assert all(a.priority_weight >= 1.0 for a in plan.allocations)


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_jalpriority_f9_recomputed_from_the_plan_equals_the_scoring(scored, scenario_id):
    plan = scored[scenario_id]["plan"]
    from_plan = round(sum(a.priority_weight * a.unmet_litres for a in plan.allocations), 1)
    assert from_plan == scored[scenario_id]["jalpriority"]["pwu"]
    assert plan.totals.unmet_litres == scored[scenario_id]["jalpriority"]["unmet"]
    assert min_coverage(scored[scenario_id]["jalpriority"]["alloc"], scored[scenario_id]["sf"]) == min(
        (a.coverage_ratio for a in plan.allocations if a.predicted_shortfall_litres > 0), default=1.0
    )


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_every_strategy_spends_at_most_the_same_supply(scored, scenario_id):
    supply = scored[scenario_id]["scenario"].supply.tanker_water_available_litres
    for name in ("jalpriority", "fcfs", "nearest_first"):
        assert sum(scored[scenario_id][name]["alloc"].values()) <= supply, name
    # the baselines exhaust the supply because requests exceed it; the optimiser never wastes water above a shortfall
    assert sum(scored[scenario_id]["fcfs"]["alloc"].values()) == supply
    assert sum(scored[scenario_id]["nearest_first"]["alloc"].values()) == supply


# --------------------------------------------------------------------------- the headline assertions
@pytest.mark.parametrize("scenario_id", DEMO_IDS)
@pytest.mark.parametrize("baseline", ["fcfs", "nearest_first"])
def test_jalpriority_has_lower_priority_weighted_unmet(scored, scenario_id, baseline):
    jp, base = scored[scenario_id]["jalpriority"], scored[scenario_id][baseline]
    assert jp["pwu"] < base["pwu"], f"{scenario_id}: jalpriority {jp['pwu']} vs {baseline} {base['pwu']}"


@pytest.mark.parametrize("scenario_id", DEMO_IDS)
@pytest.mark.parametrize("baseline", ["fcfs", "nearest_first"])
def test_jalpriority_has_higher_or_equal_minimum_coverage(scored, scenario_id, baseline):
    jp, base = scored[scenario_id]["jalpriority"], scored[scenario_id][baseline]
    assert jp["min_cov"] >= base["min_cov"], f"{scenario_id}: jalpriority {jp['min_cov']} vs {baseline} {base['min_cov']}"


@pytest.mark.parametrize("scenario_id", DEMO_IDS)
def test_jalpriority_leaves_nobody_at_zero_while_the_baselines_do(scored, scenario_id):
    assert scored[scenario_id]["jalpriority"]["min_cov"] > 0.0
    assert min(scored[scenario_id][b]["min_cov"] for b in ("fcfs", "nearest_first")) == 0.0


def test_all_three_seeds_table(scored, capsys):
    """Report every seed (visible with ``pytest -s``). The mild seed is informational: supply is plentiful there."""
    lines = [
        "",
        "| scenario | strategy | priority-weighted unmet (F-9) | total unmet L (F-12) | min coverage (F-13) |",
        "|---|---|---|---|---|",
    ]
    for sid in (MAIN_ID, SEVERE_ID, MILD_ID):
        for name in ("jalpriority", "fcfs", "nearest_first"):
            s = scored[sid][name]
            lines.append(f"| {sid} | {name} | {s['pwu']:.1f} | {s['unmet']} | {s['min_cov']:.4f} |")
    with capsys.disabled():
        print("\n".join(lines))
    for sid in SEED_IDS:  # every strategy is scored on the same shortfalls and priorities
        assert set(scored[sid]["fcfs"]["alloc"]) == set(scored[sid]["jalpriority"]["alloc"])
