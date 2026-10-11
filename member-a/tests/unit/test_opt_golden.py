"""M6/M7 golden test: the CONTRACT 5.3 example solved with the 6.6 request reproduces the 5.6 PlanResult."""
from __future__ import annotations

import pytest

from jp_a.config import Settings
from jp_a.domain.entities import PlanResult
from jp_a.services.plan_service import PlanService, build_plan

from .opt_helpers import assert_plan_invariants, golden_inputs, load_fixture, stable_dump


@pytest.fixture(scope="module")
def golden_plan() -> PlanResult:
    scenario, prediction, weights, debt = golden_inputs()
    return build_plan(scenario, prediction, weights, debt, 8.0)


@pytest.fixture(scope="module")
def expected() -> dict:
    data = load_fixture("contract_plan_result_example")
    data.pop("result_id")
    data.pop("generated_at")
    data["solver"].pop("solve_time_ms")
    return data


def test_plan_equals_contract_example_except_volatile_fields(golden_plan, expected):
    assert stable_dump(golden_plan) == expected


def test_solver_block(golden_plan):
    solver = golden_plan.solver
    assert (solver.engine, solver.status, solver.time_limit_s) == ("ortools-cp-sat", "optimal", 8.0)
    assert 0 <= solver.solve_time_ms < 9000


def test_volatile_fields_are_well_formed(golden_plan):
    data = golden_plan.model_dump(mode="json")
    PlanResult.model_validate(data)  # strict: ids, datetimes, enums
    assert data["result_id"].startswith("res_") and len(data["result_id"]) == 16


def test_allocations_and_routes(golden_plan):
    assert {a.area_id: a.allocated_litres for a in golden_plan.allocations} == {
        "ar_03": 6000,
        "ar_02": 9000,
        "ar_01": 5000,
    }
    assert [a.area_id for a in golden_plan.allocations] == ["ar_03", "ar_02", "ar_01"]
    routes = {r.tanker_id: r for r in golden_plan.routes}
    assert routes["tk_01"].path == ["dp_01", "ar_03", "ar_01", "dp_01"]  # equal-length reverse loses the tie-break
    assert routes["tk_01"].route_km == 24.4
    assert routes["tk_02"].path == ["dp_01", "ar_02", "dp_01"]
    assert (routes["tk_03"].status, routes["tk_03"].path, routes["tk_03"].load_litres) == ("unavailable", [], 0)


def test_reason_texts_are_reproduced_exactly(golden_plan):
    texts = {a.area_id: a.reason_text for a in golden_plan.allocations}
    assert texts["ar_03"] == (
        "Served in full (6,000 L). Highest priority (2.69): largest accumulated deprivation debt (12,000 L) "
        "and high vulnerability (0.80)."
    )
    assert texts["ar_02"] == (
        "Served in full (9,000 L). Priority 2.21, driven by accumulated deprivation debt of 8,000 L from "
        "earlier windows."
    )
    assert texts["ar_01"] == (
        "Partially served: 5,000 of 14,000 L (36%). 9,000 L remain unserved because all 20,000 L of tanker "
        "water went to higher-priority areas first; this area has no prior debt and low vulnerability "
        "(priority 1.41)."
    )


def test_formula_invariants_hold(golden_plan):
    scenario, prediction, weights, debt = golden_inputs()
    assert_plan_invariants(golden_plan, scenario, prediction, weights, debt)
    pwu = round(sum(a.priority_weight * a.unmet_litres for a in golden_plan.allocations), 1)  # F-9
    assert pwu == 12683.7


def test_service_method_uses_settings_and_matches_module_function(expected):
    scenario, prediction, weights, debt = golden_inputs()
    service = PlanService(Settings(a_solver_time_limit_s=8.0), scenario_repo=None, prediction_service=None)
    assert stable_dump(service.build_plan(scenario, prediction, weights, debt)) == expected


def test_repeated_golden_solves_are_identical(golden_plan):
    scenario, prediction, weights, debt = golden_inputs()
    reference = stable_dump(golden_plan)
    for _ in range(5):
        assert stable_dump(build_plan(scenario, prediction, weights, debt, 8.0)) == reference
