"""M5: F-2..F-5 priority formulas, rank order and the bottom-third rule (domain/priority.py)."""
from __future__ import annotations

import pytest

from jp_a.domain.entities import Weights
from jp_a.domain.priority import (
    bottom_third_cutoff,
    compute_area_priorities,
    in_bottom_third,
    normalized_debt,
    priority_weight,
    rank_areas,
    urgency,
    vulnerability_used,
)

from .opt_helpers import golden_inputs, scenario_dict


def test_section_5_11_worked_check():
    scenario, prediction, weights, debt = golden_inputs()
    recs = {r.area_id: r for r in compute_area_priorities(scenario, prediction, weights, debt)}
    assert {a: r.priority_weight for a, r in recs.items()} == {"ar_01": 1.4093, "ar_02": 2.2144, "ar_03": 2.6857}
    assert {a: r.urgency for a, r in recs.items()} == {"ar_01": 0.2593, "ar_02": 0.2727, "ar_03": 0.2857}
    assert {a: r.debt_normalized for a, r in recs.items()} == {"ar_01": 0.0, "ar_02": 0.6667, "ar_03": 1.0}
    assert {a: r.vulnerability for a, r in recs.items()} == {"ar_01": 0.3, "ar_02": 0.55, "ar_03": 0.8}
    assert {a: r.shortfall_litres for a, r in recs.items()} == {"ar_01": 14000, "ar_02": 9000, "ar_03": 6000}
    assert [r.area_id for r in compute_area_priorities(scenario, prediction, weights, debt)] == ["ar_01", "ar_02", "ar_03"]


def test_urgency_handles_zero_need():
    assert urgency(0, 0) == 0.0
    assert urgency(500, 0) == 0.0
    assert urgency(6000, 21000) == pytest.approx(6000 / 21000)


def test_normalized_debt_uses_max_one_floor():
    assert normalized_debt(0, 0) == 0.0
    assert normalized_debt(1, 0) == 1.0
    assert normalized_debt(8000, 12000) == pytest.approx(2 / 3)


def test_vulnerability_used_respects_availability():
    scenario, *_ = golden_inputs()
    area = scenario.areas[2]
    assert vulnerability_used(area) == 0.8
    assert vulnerability_used(area.model_copy(update={"vulnerability_available": False})) == 0.0


def test_priority_is_rounded_once_from_unrounded_components():
    w = Weights(urgency=3.0, debt=0.0, vulnerability=0.0, travel=20.0)
    # Rounding U to 0.3333 first would give 1.9999; F-5 rounds once.
    assert priority_weight(w, 1 / 3, 0.0, 0.0) == 2.0
    assert priority_weight(Weights(), 0.0, 0.0, 0.0) == 1.0


def test_debt_is_normalised_over_all_scenario_areas_and_missing_debt_is_zero():
    scenario, prediction, weights, _ = golden_inputs()
    recs = {r.area_id: r for r in compute_area_priorities(scenario, prediction, weights, {"ar_02": 4000})}
    assert recs["ar_02"].debt_normalized == 1.0
    assert recs["ar_01"].debt_litres == 0 and recs["ar_03"].debt_normalized == 0.0


def test_vulnerability_weight_zero_and_unavailable_vulnerability():
    scenario, prediction, _, debt = golden_inputs()
    data = scenario_dict(scenario)
    data["areas"][2]["vulnerability_available"] = False
    scenario2 = type(scenario).model_validate(data)
    recs = {r.area_id: r for r in compute_area_priorities(scenario2, prediction, Weights(), debt)}
    assert recs["ar_03"].vulnerability == 0.0
    assert recs["ar_03"].priority_weight == round(1 + 6000 / 21000 + 1.0, 4)


def test_unknown_debt_area_and_missing_prediction_raise():
    scenario, prediction, weights, _ = golden_inputs()
    with pytest.raises(ValueError, match="unknown area ids"):
        compute_area_priorities(scenario, prediction, weights, {"ar_09": 100})
    partial = prediction.model_copy(update={"areas": prediction.areas[:2]})
    with pytest.raises(ValueError, match="no entry"):
        compute_area_priorities(scenario, partial, weights, {})


def test_rank_order_priority_then_shortfall_then_id():
    scenario, prediction, _, _ = golden_inputs()
    flat = Weights(urgency=0.0, debt=0.0, vulnerability=0.0, travel=20.0)  # every p = 1.0
    ranked = rank_areas(compute_area_priorities(scenario, prediction, flat, {}))
    assert [r.area_id for r in ranked] == ["ar_01", "ar_02", "ar_03"]  # shortfall 14000 > 9000 > 6000
    same = prediction.model_copy(
        update={"areas": [a.model_copy(update={"predicted_shortfall_litres": 5000}) for a in prediction.areas]}
    )
    ranked = rank_areas(compute_area_priorities(scenario, same, flat, {}))
    assert [r.area_id for r in ranked] == ["ar_01", "ar_02", "ar_03"]  # full tie -> area_id


@pytest.mark.parametrize(
    "priorities,expected",
    [
        ([1.4093, 2.2144, 2.6857], [True, False, False]),  # N=3 -> exactly one area
        ([1.0, 1.0, 1.0], [False, False, False]),  # all equal -> nobody is relatively lower
        ([1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0], [True] * 4 + [False] * 6),  # ceil(10/3) = 4
        ([1.1, 1.1, 1.5, 2.0], [True, True, False, False]),  # ties with the cutoff are included
        ([2.0], [False]),
    ],
)
def test_bottom_third(priorities, expected):
    cutoff = bottom_third_cutoff(priorities)
    top = max(priorities)
    assert [in_bottom_third(p, cutoff, top) for p in priorities] == expected


def test_bottom_third_without_areas():
    assert bottom_third_cutoff([]) is None
    assert in_bottom_third(1.0, None, 1.0) is False
