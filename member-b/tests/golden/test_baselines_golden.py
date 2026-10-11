import json
from pathlib import Path

from jp_b.api.schemas import Weights, Scenario, PredictionSet
from jp_b.services.baseline_service import solve_baseline
from jp_b.domain.metrics import compute_metrics


def test_fcfs_and_nearest_first_golden():
    """
    CONTRACT §5.6 & MEMBER_B.md F7 (B5) Golden Test:
    On the §5.3 example with S = 20000:
    - FCFS -> ar_01 gets 20000 (tk_01 12000 + tk_02 8000); ar_02 and ar_03 get 0
    - total unmet = 15000; min coverage = 0.0; PWU = 2.2144 * 9000 + 2.6857 * 6000 = 36043.8
    - nearest-first gives the same allocation on this example
    """
    scen_path = Path("backend/jp_b/clients/fixtures/scn_contract_example.json")
    pred_path = Path("backend/jp_b/clients/fixtures/contract_prediction_example.json")

    with open(scen_path, "r", encoding="utf-8") as f:
        scenario = Scenario.model_validate(json.load(f))
    with open(pred_path, "r", encoding="utf-8") as f:
        prediction = PredictionSet.model_validate(json.load(f))

    weights = Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)
    debt_by_area = {"ar_01": 0, "ar_02": 8000, "ar_03": 12000}

    # Test FCFS
    fcfs_result = solve_baseline(
        strategy="fcfs",
        scenario=scenario,
        prediction=prediction,
        weights=weights,
        debt_by_area=debt_by_area,
    )

    alloc_map_fcfs = {a.area_id: a.allocated_litres for a in fcfs_result.allocations}
    assert alloc_map_fcfs["ar_01"] == 20000
    assert alloc_map_fcfs["ar_02"] == 0
    assert alloc_map_fcfs["ar_03"] == 0

    # Verify tanker deliveries
    ar01_alloc = next(a for a in fcfs_result.allocations if a.area_id == "ar_01")
    tk_deliveries = {d.tanker_id: d.litres for d in ar01_alloc.deliveries}
    assert tk_deliveries["tk_01"] == 12000
    assert tk_deliveries["tk_02"] == 8000

    fcfs_metrics = compute_metrics(
        result=fcfs_result,
        debt_by_area=debt_by_area,
        decay=0.3,
        cap_litres=None,
    )
    assert fcfs_metrics.total_unmet_litres == 15000
    assert fcfs_metrics.min_coverage_ratio == 0.0
    assert fcfs_metrics.priority_weighted_unmet == 36043.8
    assert fcfs_metrics.constraint_violations == 0

    # Test Nearest-First
    nf_result = solve_baseline(
        strategy="nearest_first",
        scenario=scenario,
        prediction=prediction,
        weights=weights,
        debt_by_area=debt_by_area,
    )

    alloc_map_nf = {a.area_id: a.allocated_litres for a in nf_result.allocations}
    assert alloc_map_nf["ar_01"] == 20000
    assert alloc_map_nf["ar_02"] == 0
    assert alloc_map_nf["ar_03"] == 0

    nf_metrics = compute_metrics(
        result=nf_result,
        debt_by_area=debt_by_area,
        decay=0.3,
        cap_litres=None,
    )
    assert nf_metrics.total_unmet_litres == 15000
    assert nf_metrics.min_coverage_ratio == 0.0
    assert nf_metrics.priority_weighted_unmet == 36043.8
    assert nf_metrics.constraint_violations == 0
