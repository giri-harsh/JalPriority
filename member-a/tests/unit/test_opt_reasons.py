"""F6: reason-code rules (domain/reasons.py) and templated explanations (services/explanation_service.py)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from jp_a.domain.reasons import (
    REASON_PARAM_KEYS,
    AreaFacts,
    PlanFacts,
    TankerFacts,
    build_reasons,
    driver_codes,
    reason_codes,
    served_code,
    unmet_cause,
)
from jp_a.services.explanation_service import explain_area, explain_plan

from .opt_helpers import REASON_PARAMS


def make_area(**overrides) -> AreaFacts:
    base = AreaFacts(
        area_id="ar_02",
        rank=2,
        priority_weight=1.8,
        urgency=0.2,
        debt_litres=0,
        debt_normalized=0.0,
        vulnerability=0.3,
        vulnerability_available=True,
        heat_exposure=0.5,
        predicted_need_litres=30_000,
        expected_piped_litres=21_000,
        shortfall_litres=9_000,
        allocated_litres=9_000,
        depot_distance_km=4.0,
        depot_metres=4_000,
    )
    return replace(base, **overrides)


def make_tanker(**overrides) -> TankerFacts:
    base = TankerFacts(
        tanker_id="tk_01",
        capacity_units=120,
        spare_units=0,
        route_limit_metres=60_000,
        route_used_metres=8_000,
        visits=frozenset({"ar_02"}),
    )
    return replace(base, **overrides)


def make_plan(**overrides) -> PlanFacts:
    base = PlanFacts(
        supply_litres=20_000,
        supply_units=200,
        tanker_capacity_litres=22_000,
        capacity_units=220,
        allocated_units=150,
        tankers=(make_tanker(),),
        travel_weight=20.0,
        alert_level=2,
        max_debt_litres=12_000,
        top_priority=2.5,
        bottom_third_cutoff=1.2,
        n_areas=3,
        solver_status="optimal",
        time_limit_s=8.0,
    )
    return replace(base, **overrides)


# --------------------------------------------------------------------------- drivers
@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"urgency": 0.3}, ["HIGH_PREDICTED_SHORTFALL"]),
        ({"urgency": 0.2999}, []),
        ({"debt_normalized": 0.5, "debt_litres": 6_000}, ["HIGH_DEBT"]),
        ({"debt_normalized": 0.4999, "debt_litres": 5_999}, []),
        ({"debt_normalized": 0.5, "debt_litres": 0}, []),
        ({"vulnerability": 0.6}, ["HIGH_VULNERABILITY"]),
        ({"vulnerability": 0.5999}, []),
        (
            {"urgency": 0.4, "debt_normalized": 1.0, "debt_litres": 12_000, "vulnerability": 0.9},
            ["HIGH_PREDICTED_SHORTFALL", "HIGH_DEBT", "HIGH_VULNERABILITY"],
        ),
    ],
)
def test_driver_thresholds_and_enum_order(overrides, expected):
    assert driver_codes(make_area(**overrides), make_plan(alert_level=0)) == expected


def test_heat_alert_only_without_another_driver():
    hot = make_area(heat_exposure=0.7)
    assert driver_codes(hot, make_plan(alert_level=2)) == ["HEAT_ALERT"]
    assert driver_codes(hot, make_plan(alert_level=3)) == ["HEAT_ALERT"]
    assert driver_codes(hot, make_plan(alert_level=1)) == []
    assert driver_codes(make_area(heat_exposure=0.69), make_plan(alert_level=3)) == []
    # CONTRACT_ISSUES CI-1: the 5.6 example lists no HEAT_ALERT for areas that already have a driver.
    assert driver_codes(replace(hot, vulnerability=0.8), make_plan(alert_level=2)) == ["HIGH_VULNERABILITY"]


def test_golden_areas_do_not_get_heat_alert():
    ar_03 = make_area(area_id="ar_03", debt_normalized=1.0, debt_litres=12_000, vulnerability=0.8, heat_exposure=0.9)
    ar_02 = make_area(debt_normalized=0.6667, debt_litres=8_000, vulnerability=0.55, heat_exposure=0.7)
    assert driver_codes(ar_03, make_plan()) == ["HIGH_DEBT", "HIGH_VULNERABILITY"]
    assert driver_codes(ar_02, make_plan()) == ["HIGH_DEBT"]


# --------------------------------------------------------------------------- served status and unmet cause
@pytest.mark.parametrize(
    "shortfall,allocated,expected",
    [(0, 0, "NO_SHORTFALL"), (9_000, 0, "NOT_SERVED"), (9_000, 9_000, "FULLY_SERVED"), (9_000, 4_000, "PARTIALLY_SERVED")],
)
def test_served_code(shortfall, allocated, expected):
    assert served_code(make_area(shortfall_litres=shortfall, allocated_litres=allocated)) == expected


def _partial(**overrides) -> AreaFacts:
    return make_area(**{"allocated_litres": 4_000, **overrides})


def test_no_cause_without_unmet_litres():
    assert unmet_cause(make_area(), make_plan()) is None
    assert unmet_cause(make_area(shortfall_litres=0, allocated_litres=0), make_plan()) is None


def test_cause_no_water_or_no_tanker_at_all():
    assert unmet_cause(_partial(allocated_litres=0), make_plan(supply_units=0, supply_litres=0)) == "SUPPLY_EXHAUSTED"
    plan = make_plan(capacity_units=0, tanker_capacity_litres=0, tankers=(), allocated_units=0)
    assert unmet_cause(_partial(allocated_litres=0), plan) == "TANKER_CAPACITY_LIMIT"


def test_over_delivery_by_whole_units_is_fully_served_without_a_cause():
    """The optimizer rounds the per-area cap UP: 9,100 L against 9,050 L is full service, not a remainder."""
    area = make_area(shortfall_litres=9_050, allocated_litres=9_100, priority_weight=1.1)  # bottom third
    assert area.unmet_litres == 0
    assert unmet_cause(area, make_plan(allocated_units=200)) is None
    assert served_code(area) == "FULLY_SERVED"
    assert reason_codes(area, make_plan(allocated_units=200)) == ["FULLY_SERVED"]


def test_small_unmet_when_water_runs_out_is_supply_exhausted():
    area = make_area(shortfall_litres=9_050, allocated_litres=9_000)
    assert unmet_cause(area, make_plan(allocated_units=200)) == "SUPPLY_EXHAUSTED"


def test_cause_unreachable_area_is_route_limit_even_when_water_runs_out():
    far = _partial(allocated_litres=0, depot_metres=31_000, depot_distance_km=31.0)
    assert unmet_cause(far, make_plan(allocated_units=200)) == "ROUTE_LIMIT"


def test_cause_stop_cut_in_route_repair_is_route_limit():
    tanker = make_tanker(spare_units=40, visits=frozenset({"ar_05"}))
    plan = make_plan(tankers=(tanker,), route_dropped=frozenset({"ar_02"}))
    assert unmet_cause(_partial(allocated_litres=0), plan) == "ROUTE_LIMIT"
    assert unmet_cause(_partial(area_id="ar_07", allocated_litres=0), plan) == "TRAVEL_COST_TRADEOFF"


def test_cause_supply_then_capacity_binding():
    assert unmet_cause(_partial(), make_plan(allocated_units=200)) == "SUPPLY_EXHAUSTED"
    plan = make_plan(supply_units=300, supply_litres=30_000, allocated_units=220)
    assert unmet_cause(_partial(), plan) == "TANKER_CAPACITY_LIMIT"


def test_cause_route_bound_blocks_every_tanker():
    tanker = make_tanker(spare_units=40, route_used_metres=55_000, visits=frozenset({"ar_05"}))
    assert unmet_cause(_partial(), make_plan(tankers=(tanker,))) == "ROUTE_LIMIT"


def test_cause_travel_cost_tradeoff():
    tanker = make_tanker(spare_units=40, route_used_metres=8_000, visits=frozenset({"ar_05"}))
    assert unmet_cause(_partial(), make_plan(tankers=(tanker,))) == "TRAVEL_COST_TRADEOFF"
    # Free travel cannot be the reason; neither can a tanker that already stops there with room to spare.
    assert unmet_cause(_partial(), make_plan(tankers=(tanker,), travel_weight=0.0)) == "LOWER_RELATIVE_PRIORITY"
    visiting = make_tanker(spare_units=40, visits=frozenset({"ar_02"}))
    assert unmet_cause(_partial(), make_plan(tankers=(visiting,))) == "LOWER_RELATIVE_PRIORITY"


# --------------------------------------------------------------------------- reason lists
def test_reason_order_matches_the_contract_example():
    area = _partial(urgency=0.35, debt_normalized=0.9, debt_litres=9_000, priority_weight=1.1)
    plan = make_plan(allocated_units=200, solver_status="heuristic")
    assert reason_codes(area, plan) == [
        "HIGH_PREDICTED_SHORTFALL",
        "HIGH_DEBT",
        "LOWER_RELATIVE_PRIORITY",
        "PARTIALLY_SERVED",
        "SUPPLY_EXHAUSTED",
        "TIME_LIMITED_SOLUTION",
    ]


def test_lower_relative_priority_is_never_duplicated():
    tanker = make_tanker(spare_units=40)  # visits ar_02 with room: the residual cause is lower relative priority
    area = _partial(priority_weight=1.1)  # bottom third and the residual cause
    codes = reason_codes(area, make_plan(tankers=(tanker,)))
    assert codes == ["LOWER_RELATIVE_PRIORITY", "PARTIALLY_SERVED"]


@pytest.mark.parametrize("allocated", [9_000, 9_100])
def test_fully_served_bottom_third_area_never_gets_lower_relative_priority(allocated):
    area = make_area(priority_weight=1.1, allocated_litres=allocated)  # bottom third (cutoff 1.2)
    for status in ("optimal", "heuristic"):
        codes = reason_codes(area, make_plan(solver_status=status))
        assert "LOWER_RELATIVE_PRIORITY" not in codes and "FULLY_SERVED" in codes
    explanation = explain_area(area, make_plan(), higher_priority_served=True)
    assert explanation.unserved_reason_code is None
    assert "remain unserved" not in explanation.reason_text


def test_no_shortfall_bottom_third_area_never_gets_lower_relative_priority():
    area = make_area(priority_weight=1.0, shortfall_litres=0, allocated_litres=0)
    assert reason_codes(area, make_plan()) == ["NO_SHORTFALL"]


def test_bottom_third_with_unmet_litres_gets_lower_relative_priority():
    area = _partial(priority_weight=1.1)
    assert reason_codes(area, make_plan(allocated_units=200)) == [
        "LOWER_RELATIVE_PRIORITY",
        "PARTIALLY_SERVED",
        "SUPPLY_EXHAUSTED",
    ]
    # Above the bottom third, the same shortage carries only its cause.
    assert reason_codes(_partial(priority_weight=1.8), make_plan(allocated_units=200)) == [
        "PARTIALLY_SERVED",
        "SUPPLY_EXHAUSTED",
    ]


def test_bottom_third_never_labels_the_top_priority():
    area = make_area(priority_weight=1.0)
    assert "LOWER_RELATIVE_PRIORITY" not in reason_codes(area, make_plan(top_priority=1.0, bottom_third_cutoff=1.0))


@pytest.mark.parametrize("status,expected", [("optimal", False), ("feasible", False), ("time_limited", True), ("heuristic", True)])
def test_time_limited_solution_reason(status, expected):
    reasons = build_reasons(make_area(), make_plan(solver_status=status, time_limit_s=0.5))
    limited = [r for r in reasons if r.code == "TIME_LIMITED_SOLUTION"]
    assert bool(limited) == expected
    if expected:
        assert limited[0].params == {"time_limit_s": 0.5}


def test_every_reason_has_exactly_the_contract_params():
    assert {k: set(v) for k, v in REASON_PARAM_KEYS.items() if k in REASON_PARAMS} == REASON_PARAMS
    area = make_area(
        urgency=0.4,
        debt_normalized=1.0,
        debt_litres=12_000,
        vulnerability=0.8,
        priority_weight=1.1,
        allocated_litres=4_000,
        depot_distance_km=4.567,
    )
    reasons = build_reasons(area, make_plan(allocated_units=200, solver_status="time_limited"))
    by_code = {r.code: r.params for r in reasons}
    assert by_code == {
        "HIGH_PREDICTED_SHORTFALL": {"predicted_shortfall_litres": 9_000, "urgency": 0.4},
        "HIGH_DEBT": {"debt_litres": 12_000},
        "HIGH_VULNERABILITY": {"vulnerability_index": 0.8},
        "LOWER_RELATIVE_PRIORITY": {"priority_weight": 1.1},
        "PARTIALLY_SERVED": {"allocated_litres": 4_000, "predicted_shortfall_litres": 9_000},
        "SUPPLY_EXHAUSTED": {"supply_available_litres": 20_000, "unmet_litres": 5_000},
        "TIME_LIMITED_SOLUTION": {"time_limit_s": 8.0},
    }
    far = build_reasons(_partial(allocated_litres=0, depot_metres=31_000, depot_distance_km=31.004), make_plan())
    assert {r.code: r.params for r in far}["ROUTE_LIMIT"] == {"distance_from_depot_km": 31.0}
    tanker = make_tanker(spare_units=40, visits=frozenset({"ar_05"}))
    travel = build_reasons(_partial(), make_plan(tankers=(tanker,)))
    assert {r.code: r.params for r in travel}["TRAVEL_COST_TRADEOFF"] == {"distance_from_depot_km": 4.0, "travel_weight": 20.0}
    none = build_reasons(make_area(shortfall_litres=0, allocated_litres=0), make_plan())
    assert {r.code: r.params for r in none}["NO_SHORTFALL"] == {}


# --------------------------------------------------------------------------- templates
def _text(area: AreaFacts, plan: PlanFacts | None = None, *, higher: bool = True) -> str:
    return explain_area(area, plan or make_plan(), higher_priority_served=higher).reason_text


def test_rank_one_variants():
    top = make_area(rank=1, priority_weight=2.6857, debt_normalized=1.0, debt_litres=12_000, vulnerability=0.8)
    assert _text(top) == (
        "Served in full (9,000 L). Highest priority (2.69): largest accumulated deprivation debt (12,000 L) "
        "and high vulnerability (0.80)."
    )
    plain = make_area(rank=1, priority_weight=1.2)
    assert _text(plain, make_plan(alert_level=0)) == "Served in full (9,000 L). Highest priority (1.20) in this window."
    shortfall = make_area(rank=1, priority_weight=1.5, urgency=0.4286)
    assert _text(shortfall, make_plan(alert_level=0)) == (
        "Served in full (9,000 L). Highest priority (1.50): high predicted shortfall (9,000 L, 43% of need)."
    )
    not_largest = make_area(rank=1, priority_weight=2.0, debt_normalized=0.9, debt_litres=9_000)
    assert "(2.00): accumulated deprivation debt (9,000 L)" in _text(not_largest, make_plan(max_debt_litres=10_000))


def test_driver_and_rank_variants():
    debt = make_area(priority_weight=2.2144, debt_normalized=0.6667, debt_litres=8_000)
    assert _text(debt) == (
        "Served in full (9,000 L). Priority 2.21, driven by accumulated deprivation debt of 8,000 L from earlier windows."
    )
    hot = make_area(priority_weight=1.5, heat_exposure=0.85)
    assert _text(hot, make_plan(alert_level=3)) == (
        "Served in full (9,000 L). Priority 1.50, driven by high heat exposure (0.85) under heat alert level 3."
    )
    plain = make_area(priority_weight=1.5)
    assert _text(plain, make_plan(alert_level=0)) == "Served in full (9,000 L). Priority 1.50 (rank 2 of 3)."


def test_partial_supply_text_matches_the_contract_example():
    ar_01 = make_area(
        area_id="ar_01",
        rank=3,
        priority_weight=1.4093,
        urgency=0.2593,
        shortfall_litres=14_000,
        allocated_litres=5_000,
    )
    assert _text(ar_01, make_plan(allocated_units=200)) == (
        "Partially served: 5,000 of 14,000 L (36%). 9,000 L remain unserved because all 20,000 L of tanker water "
        "went to higher-priority areas first; this area has no prior debt and low vulnerability (priority 1.41)."
    )


@pytest.mark.parametrize(
    "plan_overrides,area_overrides,higher,clause",
    [
        ({"allocated_units": 200}, {}, False, "all 20,000 L of tanker water is already allocated"),
        ({"supply_units": 0, "supply_litres": 0}, {"allocated_litres": 0}, True, "no tanker water is available this window"),
        (
            {"supply_units": 300, "supply_litres": 30_000, "allocated_units": 220},
            {},
            True,
            "all 22,000 L of available tanker capacity went to higher-priority areas first",
        ),
        (
            {"supply_units": 300, "supply_litres": 30_000, "allocated_units": 220},
            {},
            False,
            "all 22,000 L of available tanker capacity is already loaded",
        ),
        (
            {"capacity_units": 0, "tanker_capacity_litres": 0, "tankers": (), "allocated_units": 0},
            {"allocated_litres": 0},
            True,
            "no tanker is available this window",
        ),
        (
            {},
            {"allocated_litres": 0, "depot_metres": 31_000, "depot_distance_km": 31.0},
            True,
            "no available tanker can add this area (31 km from the depot) within its route limit",
        ),
        (
            {"tankers": (make_tanker(spare_units=40, visits=frozenset({"ar_05"})),)},
            {"depot_distance_km": 4.25},
            True,
            "an extra stop 4.25 km from the depot costs more travel (travel weight 20) than the remaining water would gain",
        ),
        (
            {"tankers": (make_tanker(spare_units=40),)},
            {},
            True,
            "higher-priority areas were served first",
        ),
        (
            {"allocated_units": 200},
            {"shortfall_litres": 9_050, "allocated_litres": 9_000},
            True,
            "all 20,000 L of tanker water went to higher-priority areas first",
        ),
    ],
)
def test_unmet_cause_clauses(plan_overrides, area_overrides, higher, clause):
    area = make_area(**{"allocated_litres": 4_000, **area_overrides})
    text = _text(area, make_plan(**plan_overrides), higher=higher)
    assert f" remain unserved because {clause}; this area has " in text
    assert text.endswith(f"(priority {area.priority_weight:.2f}).")


def test_not_served_and_profiles():
    area = make_area(allocated_litres=0, debt_litres=2_000, debt_normalized=0.2, vulnerability=0.45)
    text = _text(area, make_plan(allocated_units=200))
    assert text.startswith("Not served: 0 of 9,000 L. 9,000 L remain unserved because ")
    assert "this area has modest prior debt (2,000 L) and moderate vulnerability (0.45) (priority 1.80)." in text
    no_data = make_area(allocated_litres=0, vulnerability=0.0, vulnerability_available=False)
    assert "no prior debt and no vulnerability data" in _text(no_data, make_plan(allocated_units=200))


def test_no_shortfall_text():
    area = make_area(shortfall_litres=0, allocated_litres=0, predicted_need_litres=38_000, expected_piped_litres=40_000)
    explanation = explain_area(area, make_plan(), higher_priority_served=True)
    assert explanation.reason_text == (
        "No tanker water needed: expected piped supply (40,000 L) covers the predicted need (38,000 L)."
    )
    assert explanation.unserved_reason_code is None


@pytest.mark.parametrize("allocated,expected", [(100, "(1%)"), (19_950, "(99%)"), (7_150, "(36%)")])
def test_partial_percentages_never_read_as_zero_or_full(allocated, expected):
    area = make_area(shortfall_litres=20_000, allocated_litres=allocated)
    assert expected in _text(area, make_plan(allocated_units=200))


def test_fallback_and_time_limited_sentences():
    heuristic = _text(make_area(), make_plan(solver_status="heuristic", time_limit_s=0.5))
    assert heuristic.endswith(
        "Fallback plan: the optimizer returned no solution within its 0.5 s time limit, "
        "so areas were filled greedily in priority order."
    )
    limited = _text(make_area(), make_plan(solver_status="time_limited", time_limit_s=3.0))
    assert limited.endswith("Best plan found within the 3.0 s solver time limit; it may not be optimal.")


def test_explain_plan_uses_rank_to_decide_who_was_served_first():
    first = make_area(area_id="ar_03", rank=1, allocated_litres=0, priority_weight=2.5)
    second = make_area(area_id="ar_01", rank=2, allocated_litres=4_000)
    result = explain_plan([first, second], make_plan(allocated_units=200))
    assert "is already allocated" in result["ar_03"].reason_text  # nobody above it got water
    assert "went to higher-priority areas first" not in result["ar_03"].reason_text
    assert result["ar_01"].unserved_reason_code == "SUPPLY_EXHAUSTED"
    assert all(e.reason_text and e.reasons for e in result.values())
