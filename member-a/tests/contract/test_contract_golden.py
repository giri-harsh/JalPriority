"""MEMBER_A section 6 item 4: golden solve of the CONTRACT 5.3 example.

A Container with a tiny fake scenario repo (serving ``contract_scenario_example.json``) and a fake prediction
service (returning ``contract_prediction_example.json``) is mounted in the real app; ``POST /a/v1/plans/solve`` with
``contract_solve_request_example.json`` must reproduce ``contract_plan_result_example.json`` (CONTRACT 5.6) in every
field except ``result_id``, ``generated_at`` and ``solver.solve_time_ms``.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from jp_a.domain.entities import PlanResult
from jp_a.main import create_app

from .contract_helpers import (
    API,
    RESULT_RE,
    assert_formulas,
    assert_iso_datetimes,
    assert_plan_keys,
    bearer,
    error_envelope_ok,
    fake_container,
)

VOLATILE_TOP = ("result_id", "generated_at")


def _stable(plan: dict) -> dict:
    plan = copy.deepcopy(plan)
    for key in VOLATILE_TOP:
        plan.pop(key)
    plan["solver"].pop("solve_time_ms")
    return plan


@pytest.fixture(scope="module")
def golden(settings, make_token, load_fixture):
    scenario = load_fixture("contract_scenario_example")
    prediction = load_fixture("contract_prediction_example")
    request = load_fixture("contract_solve_request_example")
    expected = load_fixture("contract_plan_result_example")
    with TestClient(create_app(settings, fake_container(settings, scenario, prediction))) as client:
        response = client.post(f"{API}/plans/solve", json=request, headers=bearer(make_token()))
        yield {
            "client": client,
            "response": response,
            "plan": response.json(),
            "expected": expected,
            "scenario": scenario,
            "prediction": prediction,
            "request": request,
            "headers": bearer(make_token()),
        }


def test_golden_status_and_strict_shape(golden):
    assert golden["response"].status_code == 200, golden["response"].text
    plan = golden["plan"]
    PlanResult.model_validate(plan)
    assert_plan_keys(plan)
    assert_iso_datetimes(plan)
    assert RESULT_RE.match(plan["result_id"])
    assert plan["result_id"] != golden["expected"]["result_id"]


def test_golden_whole_plan_equals_the_contract_example(golden):
    assert _stable(golden["plan"]) == _stable(golden["expected"])


def test_golden_per_area_allocations(golden):
    got = {a["area_id"]: a["allocated_litres"] for a in golden["plan"]["allocations"]}
    assert got == {"ar_03": 6000, "ar_02": 9000, "ar_01": 5000}
    assert [a["area_id"] for a in golden["plan"]["allocations"]] == ["ar_03", "ar_02", "ar_01"]
    by_area = {a["area_id"]: a for a in golden["plan"]["allocations"]}
    expected = {a["area_id"]: a for a in golden["expected"]["allocations"]}
    for area_id, exp in expected.items():
        a = by_area[area_id]
        for key in (
            "rank", "predicted_shortfall_litres", "allocated_litres", "unmet_litres", "coverage_ratio",
            "priority_weight", "urgency", "debt_litres_used", "debt_normalized", "vulnerability_used",
            "is_unserved_remainder",
        ):  # fmt: skip
            assert a[key] == exp[key], f"{area_id}.{key}"


def test_golden_totals_solver_prediction_and_weights(golden):
    plan, exp = golden["plan"], golden["expected"]
    assert plan["totals"] == exp["totals"] == {
        "supply_available_litres": 20000,
        "tanker_capacity_available_litres": 22000,
        "shortfall_litres": 29000,
        "allocated_litres": 20000,
        "unmet_litres": 9000,
        "travel_km": 37.2,
    }
    assert plan["weights"] == exp["weights"]
    assert plan["prediction"] == exp["prediction"]
    assert plan["solver"]["engine"] == "ortools-cp-sat" and plan["solver"]["status"] == "optimal"
    assert plan["solver"]["time_limit_s"] == 8.0
    assert (plan["scenario_id"], plan["service_area_id"], plan["window_index"]) == ("scn_sa01_w06", "sa_01", 6)


def test_golden_routes(golden):
    assert golden["plan"]["routes"] == golden["expected"]["routes"]
    tk01, tk02, tk03 = golden["plan"]["routes"]
    assert tk01["path"] == ["dp_01", "ar_03", "ar_01", "dp_01"] and tk01["route_km"] == 24.4
    assert tk02["path"] == ["dp_01", "ar_02", "dp_01"] and tk02["route_km"] == 12.8
    assert (tk03["status"], tk03["stops"], tk03["path"]) == ("unavailable", [], [])


def test_golden_deliveries_and_unserved(golden):
    by_area = {a["area_id"]: a["deliveries"] for a in golden["plan"]["allocations"]}
    assert by_area == {a["area_id"]: a["deliveries"] for a in golden["expected"]["allocations"]}
    assert golden["plan"]["unserved"] == [{"area_id": "ar_01", "unmet_litres": 9000, "reason_code": "SUPPLY_EXHAUSTED"}]
    assert golden["plan"]["constraint_check"] == golden["expected"]["constraint_check"]


def test_golden_reasons_and_reason_text(golden):
    by_area = {a["area_id"]: a for a in golden["plan"]["allocations"]}
    for exp in golden["expected"]["allocations"]:
        got = by_area[exp["area_id"]]
        assert got["reasons"] == exp["reasons"], exp["area_id"]
        assert got["reason_text"] == exp["reason_text"], exp["area_id"]
    assert by_area["ar_03"]["reason_text"].startswith("Served in full (6,000 L).")
    assert "36%" in by_area["ar_01"]["reason_text"] and "9,000 L remain unserved" in by_area["ar_01"]["reason_text"]


def test_golden_formulas_hold_independently(golden):
    assert_formulas(golden["scenario"], golden["prediction"], golden["request"]["weights"], golden["request"]["debt_by_area"], golden["plan"])


def test_golden_is_repeatable(golden):
    again = golden["client"].post(f"{API}/plans/solve", json=golden["request"], headers=golden["headers"])
    assert again.status_code == 200
    assert _stable(again.json()) == _stable(golden["plan"])
    assert again.json()["result_id"] != golden["plan"]["result_id"]


def test_golden_container_serves_its_scenario_and_404s_others(golden):
    c, h = golden["client"], golden["headers"]
    served = c.get(f"{API}/scenarios/scn_sa01_w06", headers=h)
    assert served.status_code == 200 and served.json() == golden["scenario"]
    error_envelope_ok(c.get(f"{API}/scenarios/scn_sa01_w99", headers=h), 404, "SCENARIO_NOT_FOUND")
    body = {**golden["request"], "scenario_id": "scn_sa01_w99"}
    error_envelope_ok(c.post(f"{API}/plans/solve", json=body, headers=h), 404, "SCENARIO_NOT_FOUND")


def test_golden_fixture_self_consistency(golden):
    """The example plan itself satisfies the independent formulas (guards the fixtures against drift)."""
    assert_formulas(golden["scenario"], golden["prediction"], golden["request"]["weights"], golden["request"]["debt_by_area"], golden["expected"])
