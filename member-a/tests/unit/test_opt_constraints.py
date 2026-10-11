"""M5: F-8 route length and F-15 constraint checks (domain/constraints.py)."""
from __future__ import annotations

import pytest

from jp_a.domain.constraints import (
    build_constraint_check,
    count_violations,
    distance,
    find_violations,
    km_to_metres_down,
    km_to_metres_up,
    litres_to_units,
    litres_to_units_up,
    path_length_km,
    route_km,
)
from jp_a.domain.entities import PlanResult

from .opt_helpers import golden_inputs, load_fixture


@pytest.fixture()
def golden_plan() -> dict:
    return load_fixture("contract_plan_result_example")


def _parts(plan: dict):
    parsed = PlanResult.model_validate(plan)
    return parsed.allocations, parsed.routes


def test_route_km_matches_contract_routes():
    scenario, *_ = golden_inputs()
    d = scenario.distance_km
    assert route_km(["dp_01", "ar_03", "ar_01", "dp_01"], d) == 24.4
    assert route_km(["dp_01", "ar_01", "ar_03", "dp_01"], d) == 24.4
    assert route_km(["dp_01", "ar_02", "dp_01"], d) == 12.8
    assert route_km([], d) == 0.0
    assert path_length_km(["dp_01"], d) == 0.0


def test_distance_falls_back_to_symmetric_entry_and_rejects_unknown_nodes():
    matrix = {"dp_01": {"ar_01": 2.5}}
    assert distance(matrix, "ar_01", "dp_01") == 2.5
    assert distance(matrix, "ar_01", "ar_01") == 0.0
    with pytest.raises(KeyError):
        distance(matrix, "dp_01", "ar_09")


def test_unit_and_metre_conversions():
    assert litres_to_units(14050) == 140 and litres_to_units(99) == 0 and litres_to_units(-5) == 0
    assert litres_to_units_up(6821) == 69 and litres_to_units_up(6800) == 68 and litres_to_units_up(1) == 1
    assert litres_to_units_up(0) == 0 and litres_to_units_up(-5) == 0
    assert km_to_metres_up(11.8) == 11800 and km_to_metres_up(3.1004) == 3101
    assert km_to_metres_down(60.0) == 60000 and km_to_metres_down(59.9999) == 59999


def test_contract_example_has_no_violations(golden_plan):
    scenario, *_ = golden_inputs()
    allocations, routes = _parts(golden_plan)
    check = build_constraint_check(scenario, allocations, routes)
    assert check.model_dump() == golden_plan["constraint_check"]
    assert count_violations(scenario, allocations, routes) == 0


def test_water_cap_exceeded(golden_plan):
    scenario, *_ = golden_inputs()
    scenario = scenario.model_copy(update={"supply": scenario.supply.model_copy(update={"tanker_water_available_litres": 19900})})
    allocations, routes = _parts(golden_plan)
    check = build_constraint_check(scenario, allocations, routes)
    assert not check.water_cap_ok and check.capacity_ok and check.route_limit_ok and check.unavailable_tanker_ok
    assert [v.code for v in check.violations] == ["WATER_CAP_EXCEEDED"]


def test_tanker_capacity_is_checked_against_the_scenario_not_the_route(golden_plan):
    scenario, *_ = golden_inputs()
    tankers = [t.model_copy(update={"capacity_litres": 10000}) if t.tanker_id == "tk_01" else t for t in scenario.tankers]
    scenario = scenario.model_copy(update={"tankers": tankers})
    allocations, routes = _parts(golden_plan)  # routes still claim capacity 12000
    violations = find_violations(scenario, allocations, routes)
    assert [v.code for v in violations] == ["TANKER_CAPACITY_EXCEEDED"]
    assert "tk_01" in violations[0].detail


def test_route_limit_uses_recomputed_km_and_tolerance(golden_plan):
    scenario, *_ = golden_inputs()
    allocations, routes = _parts(golden_plan)
    for limit, expected in [(24.39, 0), (24.38, 1)]:  # 24.4 <= 24.39 + 0.01 passes; 24.38 + 0.01 does not
        tankers = [t.model_copy(update={"max_route_km": limit}) if t.tanker_id == "tk_01" else t for t in scenario.tankers]
        s = scenario.model_copy(update={"tankers": tankers})
        codes = [v.code for v in find_violations(s, allocations, routes)]
        assert codes.count("ROUTE_LIMIT_EXCEEDED") == expected
    # A route that lies about its km is re-measured on the matrix.
    lying = [r.model_copy(update={"path": ["dp_01", "ar_03", "ar_02", "ar_01", "ar_03", "dp_01"]}) if r.tanker_id == "tk_01" else r for r in routes]
    tankers = [t.model_copy(update={"max_route_km": 30.0}) for t in scenario.tankers]
    assert [v.code for v in find_violations(scenario.model_copy(update={"tankers": tankers}), allocations, lying)] == [
        "ROUTE_LIMIT_EXCEEDED"
    ]


def test_each_delivery_by_unavailable_or_unknown_tanker_counts(golden_plan):
    scenario, *_ = golden_inputs()
    plan = dict(golden_plan)
    plan["allocations"] = [dict(a) for a in golden_plan["allocations"]]
    plan["allocations"][0]["deliveries"] = [
        {"tanker_id": "tk_03", "stop_order": 1, "litres": 3000},
        {"tanker_id": "tk_09", "stop_order": 1, "litres": 3000},
    ]
    allocations, routes = _parts(plan)
    check = build_constraint_check(scenario, allocations, routes)
    assert not check.unavailable_tanker_ok
    assert [v.code for v in check.violations] == ["UNAVAILABLE_TANKER_USED", "UNAVAILABLE_TANKER_USED"]
    assert count_violations(scenario, allocations, routes) == 2
