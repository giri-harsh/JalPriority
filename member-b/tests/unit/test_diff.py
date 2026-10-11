from datetime import datetime, timezone
import pytest

from jp_b.domain.diff import compute_plan_diff
from jp_b.api.schemas import (
    PlanRecord,
    PlanResult,
    Allocation,
    Delivery,
    Metrics,
    Totals,
    Weights,
    ConstraintCheck,
    PredictionInfo,
    SolverInfo,
)


def _make_alloc(area_id: str, allocated: int, deliveries: list, rank: int = 1):
    return Allocation(
        area_id=area_id,
        rank=rank,
        predicted_shortfall_litres=5000,
        allocated_litres=allocated,
        unmet_litres=max(0, 5000 - allocated),
        coverage_ratio=min(1.0, allocated / 5000.0),
        priority_weight=1.5,
        urgency=0.5,
        debt_litres_used=0,
        debt_normalized=0.0,
        vulnerability_used=0.0,
        deliveries=deliveries,
        reasons=[],
        reason_text="test",
        is_unserved_remainder=False,
    )


def _make_dummy_plan(allocations, metrics=None, plan_id="pln_test"):
    if metrics is None:
        metrics = Metrics(
            schema_version="1.0",
            priority_weighted_unmet=100.0,
            deprivation_debt_remaining_litres=1000,
            total_unmet_litres=500,
            min_coverage_ratio=0.5,
            total_travel_km=10.0,
            constraint_violations=0,
            replanning_time_ms=None,
        )
    result = PlanResult(
        schema_version="1.0",
        result_id="res_test",
        generated_at=datetime.now(timezone.utc),
        scenario_id="scn_test",
        service_area_id="sa_01",
        window_index=1,
        strategy="jalpriority",
        weights=Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0),
        solver=SolverInfo(
            engine="ortools-cp-sat",
            status="optimal",
            solve_time_ms=50,
            time_limit_s=8.0,
        ),
        prediction=PredictionInfo(
            model_version="xgb-sa-v1",
            latency_ms=10,
            run_mae_litres=100.0,
        ),
        allocations=allocations,
        unserved=[],
        routes=[],
        totals=Totals(
            supply_available_litres=20000,
            tanker_capacity_available_litres=20000,
            shortfall_litres=10000,
            allocated_litres=10000,
            unmet_litres=500,
            travel_km=10.0,
        ),
        constraint_check=ConstraintCheck(
            water_cap_ok=True,
            capacity_ok=True,
            route_limit_ok=True,
            unavailable_tanker_ok=True,
            violations=[],
        ),
    )
    return PlanRecord(
        schema_version="1.0",
        plan_id=plan_id,
        status="recommended",
        created_at=datetime.now(timezone.utc),
        created_by="officer_01",
        scenario_id="scn_test",
        service_area_id="sa_01",
        window_index=1,
        supersedes_plan_id=None,
        replan_of_plan_id=None,
        disruption=None,
        is_synthetic=True,
        result=result,
        metrics=metrics,
        decision=None,
    )


def test_diff_all_six_classifications_and_formulas():
    """
    Test all six classification categories:
    - newly_served: 0 -> 4000
    - dropped: 4000 -> 0
    - increased: 3000 -> 5000
    - decreased: 6000 -> 4000
    - reassigned: 5000 -> 5000 with tanker or order change
    - unchanged: 2000 -> 2000 with same tanker and order
    """
    before_allocs = [
        # ar_01: dropped (4000 -> 0)
        _make_alloc("ar_01", 4000, [Delivery(tanker_id="tk_01", stop_order=1, litres=4000)], rank=1),
        # ar_02: newly_served (0 -> 4000)
        _make_alloc("ar_02", 0, [], rank=2),
        # ar_03: increased (3000 -> 5000)
        _make_alloc("ar_03", 3000, [Delivery(tanker_id="tk_02", stop_order=1, litres=3000)], rank=3),
        # ar_04: decreased (6000 -> 4000)
        _make_alloc("ar_04", 6000, [Delivery(tanker_id="tk_02", stop_order=2, litres=6000)], rank=4),
        # ar_05: reassigned (5000 -> 5000, tanker tk_01 -> tk_03)
        _make_alloc("ar_05", 5000, [Delivery(tanker_id="tk_01", stop_order=2, litres=5000)], rank=5),
        # ar_06: unchanged (2000 -> 2000, same tk_03 stop 1)
        _make_alloc("ar_06", 2000, [Delivery(tanker_id="tk_03", stop_order=1, litres=2000)], rank=6),
    ]

    after_allocs = [
        # ar_01: dropped (0 L)
        _make_alloc("ar_01", 0, [], rank=6),
        # ar_02: newly served (4000 L)
        _make_alloc("ar_02", 4000, [Delivery(tanker_id="tk_01", stop_order=1, litres=4000)], rank=1),
        # ar_03: increased (5000 L)
        _make_alloc("ar_03", 5000, [Delivery(tanker_id="tk_02", stop_order=1, litres=5000)], rank=2),
        # ar_04: decreased (4000 L)
        _make_alloc("ar_04", 4000, [Delivery(tanker_id="tk_02", stop_order=2, litres=4000)], rank=3),
        # ar_05: reassigned (5000 L, tanker changed to tk_03)
        _make_alloc("ar_05", 5000, [Delivery(tanker_id="tk_03", stop_order=2, litres=5000)], rank=4),
        # ar_06: unchanged (2000 L, tk_03 stop 1)
        _make_alloc("ar_06", 2000, [Delivery(tanker_id="tk_03", stop_order=1, litres=2000)], rank=5),
    ]

    plan_before = _make_dummy_plan(before_allocs, plan_id="pln_before")
    plan_after = _make_dummy_plan(after_allocs, plan_id="pln_after")

    diff = compute_plan_diff(plan_before, plan_after)

    diff_map = {d.area_id: d for d in diff.areas}

    # ar_01: dropped
    assert diff_map["ar_01"].change == "dropped"
    assert diff_map["ar_01"].litres_before == 4000
    assert diff_map["ar_01"].litres_after == 0
    assert diff_map["ar_01"].delta == -4000
    assert diff_map["ar_01"].tankers_before == ["tk_01"]
    assert diff_map["ar_01"].tankers_after == []

    # ar_02: newly_served
    assert diff_map["ar_02"].change == "newly_served"
    assert diff_map["ar_02"].litres_before == 0
    assert diff_map["ar_02"].litres_after == 4000
    assert diff_map["ar_02"].delta == 4000
    assert diff_map["ar_02"].tankers_before == []
    assert diff_map["ar_02"].tankers_after == ["tk_01"]

    # ar_03: increased
    assert diff_map["ar_03"].change == "increased"
    assert diff_map["ar_03"].litres_before == 3000
    assert diff_map["ar_03"].litres_after == 5000
    assert diff_map["ar_03"].delta == 2000

    # ar_04: decreased
    assert diff_map["ar_04"].change == "decreased"
    assert diff_map["ar_04"].litres_before == 6000
    assert diff_map["ar_04"].litres_after == 4000
    assert diff_map["ar_04"].delta == -2000

    # ar_05: reassigned
    assert diff_map["ar_05"].change == "reassigned"
    assert diff_map["ar_05"].litres_before == 5000
    assert diff_map["ar_05"].litres_after == 5000
    assert diff_map["ar_05"].delta == 0
    assert diff_map["ar_05"].tankers_before == ["tk_01"]
    assert diff_map["ar_05"].tankers_after == ["tk_03"]

    # ar_06: unchanged
    assert diff_map["ar_06"].change == "unchanged"
    assert diff_map["ar_06"].litres_before == 2000
    assert diff_map["ar_06"].litres_after == 2000
    assert diff_map["ar_06"].delta == 0
    assert diff_map["ar_06"].tankers_before == ["tk_03"]
    assert diff_map["ar_06"].tankers_after == ["tk_03"]

    # Summary checks:
    # areas_changed: ar_01, ar_02, ar_03, ar_04, ar_05 (5 areas)
    assert diff.summary.areas_changed == 5

    # litres_moved: Σ|delta| / 2
    # |delta|: |-4000| + |4000| + |2000| + |-2000| + 0 + 0 = 12000
    # 12000 / 2 = 6000
    assert diff.summary.litres_moved == 6000
