"""Demo-property report on model predictions (jp_a.ml.demo_report)."""
from __future__ import annotations

import copy

import pytest

from jp_a.domain.entities import PredictionSet, Scenario
from jp_a.ml.demo_report import MIN_SHORTFALL_TO_SUPPLY, demo_property_report

from .ml_helpers import load_scenario_fixture

# area_id: (depot_km, request minute, vulnerability, requested, predicted shortfall, history template index)
# Template 0 (contract ar_01) has no unmet need; template 2 (contract ar_03) leaves deprivation debt.
LAYOUT = {
    "ar_01": (1.0, 0, 0.20, 20_000, 5_000, 0),  # near, early, low vulnerability, over-requests
    "ar_02": (2.0, 10, 0.30, 6_000, 6_000, 0),
    "ar_03": (3.0, 20, 0.50, 7_000, 8_000, 0),
    "ar_04": (9.0, 30, 0.70, 4_000, 10_000, 2),  # far, late, vulnerable, under-requests, indebted
    "ar_05": (10.0, 40, 0.80, 4_000, 12_000, 2),  # far, late, vulnerable, under-requests, indebted
    "ar_06": (11.0, 50, 0.50, 3_000, 9_000, 2),  # far and late but not vulnerable enough
}
TOTAL_SHORTFALL = sum(v[4] for v in LAYOUT.values())


def _scenario(supply: int = 20_000) -> Scenario:
    data = copy.deepcopy(load_scenario_fixture())
    templates = data["areas"]
    areas = []
    for area_id, (_, minute, vuln, requested, _, tpl) in LAYOUT.items():
        area = copy.deepcopy(templates[tpl])
        area.update(
            area_id=area_id,
            vulnerability_index=vuln,
            requested_litres=requested,
            request_time=f"2026-05-21T05:{minute:02d}:00.000Z",
        )
        areas.append(area)
    data["areas"] = areas
    # Areas sit on a line through the depot, so every distance is |position_a - position_b|.
    position = {"dp_01": 0.0, **{aid: v[0] for aid, v in LAYOUT.items()}}
    data["distance_km"] = {a: {b: round(abs(pa - pb), 2) for b, pb in position.items()} for a, pa in position.items()}
    data["supply"]["tanker_water_available_litres"] = supply
    return Scenario.model_validate(data)


def _predictions(scenario: Scenario, shortfall: dict[str, int] | None = None) -> PredictionSet:
    shortfall = shortfall or {aid: v[4] for aid, v in LAYOUT.items()}
    top = [
        {"feature": "last_need_litres", "contribution_litres": 900.0},
        {"feature": "population", "contribution_litres": 500.0},
        {"feature": "heat_index_c", "contribution_litres": 100.0},
    ]
    areas = []
    for area in scenario.areas:
        piped = area.expected_piped_litres
        areas.append(
            {
                "area_id": area.area_id,
                "predicted_need_litres": piped + shortfall[area.area_id],
                "expected_piped_litres": piped,
                "predicted_shortfall_litres": shortfall[area.area_id],
                "baseline_last_need_litres": area.history[-1].need_litres,
                "top_features": top,
            }
        )
    return PredictionSet.model_validate(
        {
            "schema_version": "1.0",
            "scenario_id": scenario.scenario_id,
            "model_version": "xgb-sa-v1",
            "generated_at": "2026-10-10T09:15:02.050Z",
            "latency_ms": 5,
            "run_mae_litres": None,
            "is_simulation": True,
            "areas": areas,
        }
    )


def test_property_holds_for_the_designed_layout():
    scenario = _scenario()
    report = demo_property_report(scenario, _predictions(scenario))
    assert report.qualifying_area_ids == ["ar_04", "ar_05"]
    assert report.over_requesting_area_ids == ["ar_01"]
    assert report.total_predicted_shortfall_litres == TOTAL_SHORTFALL
    assert report.shortfall_to_supply == round(TOTAL_SHORTFALL / 20_000, 4)
    assert report.ratio_ok and report.property_holds


def test_ranks_and_debt_per_area():
    scenario = _scenario()
    rows = {r.area_id: r for r in demo_property_report(scenario, _predictions(scenario)).areas}
    assert [rows[a].distance_rank for a in LAYOUT] == [6, 5, 4, 3, 2, 1]
    assert [rows[a].request_rank for a in LAYOUT] == [6, 5, 4, 3, 2, 1]
    assert rows["ar_01"].debt_litres == 0
    assert rows["ar_04"].debt_litres > 0
    assert not rows["ar_06"].qualifies  # vulnerability 0.5 < 0.6


def test_property_fails_when_supply_covers_the_shortfall():
    scenario = _scenario(supply=int(TOTAL_SHORTFALL / MIN_SHORTFALL_TO_SUPPLY) + 1_000)
    report = demo_property_report(scenario, _predictions(scenario))
    assert not report.ratio_ok
    assert not report.property_holds


def test_property_fails_when_vulnerable_areas_request_enough():
    scenario = _scenario()
    shortfall = {aid: v[4] for aid, v in LAYOUT.items()} | {"ar_04": 3_000, "ar_05": 3_000}
    report = demo_property_report(scenario, _predictions(scenario, shortfall))
    assert report.qualifying_area_ids == []
    assert not report.property_holds


def test_to_dict_carries_the_verdicts():
    scenario = _scenario()
    payload = demo_property_report(scenario, _predictions(scenario)).to_dict()
    assert payload["property_holds"] is True and payload["ratio_ok"] is True
    assert len(payload["areas"]) == len(LAYOUT)


def test_mismatched_predictions_are_rejected():
    scenario = _scenario()
    predictions = _predictions(scenario)
    with pytest.raises(ValueError, match="predictions are for"):
        demo_property_report(scenario, predictions.model_copy(update={"scenario_id": "scn_sa01_w06_mild"}))
    partial = predictions.model_copy(update={"areas": predictions.areas[:-1]})
    with pytest.raises(ValueError, match="missing areas"):
        demo_property_report(scenario, partial)
