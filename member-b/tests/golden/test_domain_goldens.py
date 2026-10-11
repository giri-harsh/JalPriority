import json
from pathlib import Path
from jp_b.api.schemas import Weights, PlanResult, Scenario
from jp_b.domain.priority import (
    compute_area_priority,
    compute_priorities_for_scenario,
)
from jp_b.domain.metrics import compute_metrics
from jp_b.domain.constraints import check_constraints

def test_contract_section_5_11_priority_worked_check():
    """
    CONTRACT §5.11 Worked check on the example:
    With D = {0, 8000, 12000} -> D̂ = {0, 0.6667, 1}:
    - p_01 = 1 + 0.2593 + 0 + 0.15 = 1.4093
    - p_02 = 1 + 0.2727 + 0.6667 + 0.275 = 2.2144
    - p_03 = 1 + 0.2857 + 1 + 0.4 = 2.6857
    """
    weights = Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)

    # ar_01: need=54000, piped=40000, debt=0, max_debt=12000, vuln=0.30, avail=True
    p1 = compute_area_priority(
        area_id="ar_01",
        predicted_need_litres=54000,
        expected_piped_litres=40000,
        debt_litres=0,
        max_debt_litres=12000,
        vulnerability_index=0.30,
        vulnerability_available=True,
        weights=weights,
    )
    assert p1.shortfall_litres == 14000
    assert p1.urgency == 0.2593
    assert p1.debt_normalized == 0.0
    assert p1.vulnerability_used == 0.3
    assert p1.priority_weight == 1.4093

    # ar_02: need=33000, piped=24000, debt=8000, max_debt=12000, vuln=0.55, avail=True
    p2 = compute_area_priority(
        area_id="ar_02",
        predicted_need_litres=33000,
        expected_piped_litres=24000,
        debt_litres=8000,
        max_debt_litres=12000,
        vulnerability_index=0.55,
        vulnerability_available=True,
        weights=weights,
    )
    assert p2.shortfall_litres == 9000
    assert p2.urgency == 0.2727
    assert p2.debt_normalized == 0.6667
    assert p2.vulnerability_used == 0.55
    assert p2.priority_weight == 2.2144

    # ar_03: need=21000, piped=15000, debt=12000, max_debt=12000, vuln=0.80, avail=True
    p3 = compute_area_priority(
        area_id="ar_03",
        predicted_need_litres=21000,
        expected_piped_litres=15000,
        debt_litres=12000,
        max_debt_litres=12000,
        vulnerability_index=0.80,
        vulnerability_available=True,
        weights=weights,
    )
    assert p3.shortfall_litres == 6000
    assert p3.urgency == 0.2857
    assert p3.debt_normalized == 1.0
    assert p3.vulnerability_used == 0.8
    assert p3.priority_weight == 2.6857


def test_contract_section_5_7_metrics_golden():
    """
    CONTRACT §5.7 & MEMBER_B.md B6:
    compute_metrics on the §5.6 example result with debt {0, 8000, 12000} and λ = 0.3
    returns the §5.7 Metrics object exactly.
    """
    fixture_path = Path("backend/jp_b/clients/fixtures/contract_plan_result_example.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        plan_result_data = json.load(f)

    plan_result = PlanResult.model_validate(plan_result_data)
    debt_by_area = {"ar_01": 0, "ar_02": 8000, "ar_03": 12000}

    metrics = compute_metrics(
        result=plan_result,
        debt_by_area=debt_by_area,
        decay=0.3,
        cap_litres=None,
        replanning_time_ms=None,
    )

    # Compare with binding values in §5.7
    assert metrics.priority_weighted_unmet == 12683.7
    assert metrics.deprivation_debt_remaining_litres == 23000
    assert metrics.total_unmet_litres == 9000
    assert metrics.min_coverage_ratio == 0.3571
    assert metrics.total_travel_km == 37.2
    assert metrics.constraint_violations == 0
    assert metrics.replanning_time_ms is None
