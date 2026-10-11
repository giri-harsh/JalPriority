import json
import pytest
from pathlib import Path
from jp_b.api.schemas import Weights, PlanResult, Scenario
from jp_b.domain.priority import (
    compute_shortfall,
    compute_urgency,
    normalize_debt,
    get_vulnerability,
    compute_priority,
    compute_area_priority,
    compute_priorities_for_scenario,
)
from jp_b.domain.constraints import check_constraints
from jp_b.domain.metrics import compute_unmet, compute_coverage, update_debt, compute_metrics


def test_priority_edge_cases():
    # 1. Zero predicted need
    assert compute_shortfall(0, 5000) == 0
    assert compute_urgency(0, 0) == 0.0

    # 2. Piped exceeds need
    assert compute_shortfall(10000, 15000) == 0

    # 3. All debt 0
    assert normalize_debt(0, 0) == 0.0

    # 4. Vulnerability unavailable
    assert get_vulnerability(0.85, False) == 0.0
    assert get_vulnerability(0.85, True) == 0.85

    # 5. Base priority with all zero components
    w = Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)
    assert compute_priority(0.0, 0.0, 0.0, w) == 1.0


def test_debt_update_and_cap():
    # Default decay 0.3
    # D' = round((1 - 0.3) * 10000 + 3000) = round(7000 + 3000) = 10000
    assert update_debt(10000, 3000, decay=0.3) == 10000

    # Cap applied
    assert update_debt(10000, 3000, decay=0.3, cap_litres=8000) == 8000

    # Zero debt with unmet need
    assert update_debt(0, 5000, decay=0.3) == 5000

    # High decay (1.0 -> debt fully forgotten)
    assert update_debt(10000, 2000, decay=1.0) == 2000


def test_coverage_and_unmet():
    assert compute_unmet(10000, 6000) == 4000
    assert compute_unmet(10000, 12000) == 0

    assert compute_coverage(10000, 5000) == 0.5
    assert compute_coverage(10000, 12000) == 1.0
    assert compute_coverage(0, 0) == 1.0


def test_independent_constraint_violations():
    fixture_path = Path("backend/jp_b/clients/fixtures/contract_plan_result_example.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Clean plan has 0 violations
    res = check_constraints(data)
    assert res.violation_count == 0
    assert res.water_cap_ok is True
    assert res.capacity_ok is True
    assert res.route_limit_ok is True
    assert res.unavailable_tanker_ok is True

    # 1. Total water cap violation
    bad_data = json.loads(json.dumps(data))
    bad_data["allocations"][0]["allocated_litres"] = 50000
    res1 = check_constraints(bad_data)
    assert res1.water_cap_ok is False
    assert res1.violation_count == 1
    assert res1.violations[0]["code"] == "WATER_CAP_EXCEEDED"

    # 2. Tanker capacity violation
    bad_data2 = json.loads(json.dumps(data))
    bad_data2["routes"][0]["load_litres"] = 15000  # capacity is 12000
    res2 = check_constraints(bad_data2)
    assert res2.capacity_ok is False
    assert res2.violation_count == 1
    assert res2.violations[0]["code"] == "TANKER_CAPACITY_EXCEEDED"

    # 3. Route limit violation (> max_route_km + 0.01)
    bad_data3 = json.loads(json.dumps(data))
    bad_data3["routes"][0]["route_km"] = 60.05  # max is 60.0
    res3 = check_constraints(bad_data3)
    assert res3.route_limit_ok is False
    assert res3.violation_count == 1
    assert res3.violations[0]["code"] == "ROUTE_LIMIT_EXCEEDED"

    # 4. Unavailable tanker makes delivery
    bad_data4 = json.loads(json.dumps(data))
    # tk_03 is unavailable in example; give it 2 deliveries
    bad_data4["routes"][2]["stops"] = [
        {"stop_order": 1, "area_id": "ar_01", "litres": 2000},
        {"stop_order": 2, "area_id": "ar_02", "litres": 3000},
    ]
    res4 = check_constraints(bad_data4)
    assert res4.unavailable_tanker_ok is False
    assert res4.violation_count == 2
    assert res4.violations[0]["code"] == "UNAVAILABLE_TANKER_USED"


def test_scenario_priority_computation():
    fixture_path = Path("backend/jp_b/clients/fixtures/scn_contract_example.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenario_data = json.load(f)

    pred_fixture_path = Path("backend/jp_b/clients/fixtures/contract_prediction_example.json")
    with open(pred_fixture_path, "r", encoding="utf-8") as f:
        pred_data = json.load(f)

    weights = Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)
    debt_by_area = {"ar_01": 0, "ar_02": 8000, "ar_03": 12000}

    results = compute_priorities_for_scenario(
        areas=scenario_data["areas"],
        debt_by_area=debt_by_area,
        weights=weights,
        predictions=pred_data["areas"],
    )

    assert results["ar_01"].priority_weight == 1.4093
    assert results["ar_02"].priority_weight == 2.2144
    assert results["ar_03"].priority_weight == 2.6857
