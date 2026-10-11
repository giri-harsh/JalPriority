"""Hardening properties: 10-area / 4-tanker random scenarios meet the wall-time targets and survive every disruption.

* plan at the production default limit (8.0 s) takes < 10 s; at 3.0 s it takes < time_limit_s + 1.0;
* the replan chain (disruption applied with the CONTRACT 5.12 functions, then a 3.0 s solve) takes < 4 s;
* every plan passes the independent checker (``property_helpers.plan_problems``) with 0 violations.

The prediction is taken from the generator (the model is not under test here); a heat alert changes the scenario
but not the generated prediction, which is irrelevant to the solver-time property.
"""
from __future__ import annotations

import time

import pytest

from jp_a.domain.disruptions import apply_disruption, validate_disruption
from jp_a.services.plan_service import build_plan

from .property_helpers import plan_problems, random_case

SEEDS = range(100, 108)
SUPPLY_RATIOS = (0.3, 0.55, 0.8, 1.0)
APPLIED_AT = "2026-05-21T06:00:00.000Z"


def _case(seed: int):
    ratio = SUPPLY_RATIOS[seed % len(SUPPLY_RATIOS)]
    return random_case(seed, n_areas=10, n_tankers=4, unavailable=0, supply_ratio=ratio, radius_km=10.0, max_route_km=60.0)


def _disruptions(scenario) -> list[tuple[str, dict]]:
    level = scenario.heat.alert_level
    first_available = next(t.tanker_id for t in scenario.tankers if t.status == "available")
    return [
        ("tanker_down", {"tanker_id": first_available}),
        ("supply_cut", {"fraction": 0.3}),
        ("heat_alert", {"alert_level": level + 1 if level < 3 else level - 1}),
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_default_limit_plan_is_fast_and_sound(seed):
    scenario, prediction, weights, debt = _case(seed)
    started = time.perf_counter()
    plan = build_plan(scenario, prediction, weights, debt, 8.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 10.0, f"seed {seed}: {elapsed:.2f}s"
    assert plan_problems(plan, scenario, prediction, weights, debt) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_three_second_plan_stays_within_limit_plus_one_second(seed):
    scenario, prediction, weights, debt = _case(seed)
    started = time.perf_counter()
    plan = build_plan(scenario, prediction, weights, debt, 3.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0 + 1.0, f"seed {seed}: {elapsed:.2f}s"
    assert plan_problems(plan, scenario, prediction, weights, debt) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_every_disruption_replans_under_four_seconds_with_zero_violations(seed):
    scenario, prediction, weights, debt = _case(seed)
    for dtype, params in _disruptions(scenario):
        started = time.perf_counter()
        normalised = validate_disruption(scenario, dtype, params)
        derived = apply_disruption(scenario, dtype, normalised, APPLIED_AT)
        plan = build_plan(derived, prediction, weights, debt, 3.0)
        elapsed = time.perf_counter() - started
        assert elapsed < 4.0, f"seed {seed} {dtype}: {elapsed:.2f}s"
        assert plan_problems(plan, derived, prediction, weights, debt) == [], f"seed {seed} {dtype}"
        assert derived.parent_scenario_id == scenario.scenario_id


@pytest.mark.parametrize("seed", SEEDS)
def test_tanker_down_removes_its_capacity_and_its_route(seed):
    """The lost tanker leaves the available capacity, is marked unavailable and carries no stops."""
    scenario, prediction, weights, debt = _case(seed)
    base = build_plan(scenario, prediction, weights, debt, 3.0)
    dtype, params = _disruptions(scenario)[0]
    derived = apply_disruption(scenario, dtype, validate_disruption(scenario, dtype, params), APPLIED_AT)
    plan = build_plan(derived, prediction, weights, debt, 3.0)
    assert plan.totals.tanker_capacity_available_litres < base.totals.tanker_capacity_available_litres
    route = next(r for r in plan.routes if r.tanker_id == params["tanker_id"])
    assert route.status == "unavailable" and route.stops == [] and route.path == []
