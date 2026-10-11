"""MEMBER_A F1 demo property, asserted on the REAL model's predictions for the committed seed scenarios.

``test_data_demo_property.py`` checks the generator against the hidden truth; this module checks the shortfall
the API actually serves (committed booster + real ScenarioRepository), which is what the dispatch plan uses.
"""
from __future__ import annotations

import pytest

from jp_a.api.deps import build_container
from jp_a.ml.demo_report import (
    LOW_VULNERABILITY,
    MIN_QUALIFYING_AREAS,
    MIN_SHORTFALL_TO_SUPPLY,
    MIN_VULNERABILITY,
    RANK_K,
    DemoReport,
    demo_property_report,
)

from .ml_helpers import REAL_DATA_DIR, REAL_MODELS_DIR, SEED_SCENARIO_IDS


@pytest.fixture(scope="module")
def reports(settings) -> dict[str, DemoReport]:
    # Read-only: predictions are computed in memory, nothing is written under data/.
    container = build_container(settings.model_copy(update={"a_data_dir": REAL_DATA_DIR, "a_models_dir": REAL_MODELS_DIR}))
    out = {}
    for scenario_id in SEED_SCENARIO_IDS:
        scenario = container.scenario_repo.get(scenario_id)
        out[scenario_id] = demo_property_report(scenario, container.prediction_service.get_predictions(scenario_id))
    return out


def test_main_demo_property_holds_on_real_predictions(reports):
    report = reports["scn_sa01_w06"]
    assert report.property_holds, report.to_dict()


def test_main_demo_has_enough_far_late_vulnerable_under_requesters(reports):
    report = reports["scn_sa01_w06"]
    assert len(report.qualifying_area_ids) >= MIN_QUALIFYING_AREAS
    rows = {r.area_id: r for r in report.areas}
    for area_id in report.qualifying_area_ids:
        row = rows[area_id]
        assert row.vulnerability_index >= MIN_VULNERABILITY
        assert row.distance_rank <= RANK_K
        assert row.request_rank <= RANK_K
        assert row.requested_litres < row.predicted_shortfall_litres
        assert row.debt_litres > 0


def test_main_demo_has_a_near_early_over_requester(reports):
    report = reports["scn_sa01_w06"]
    n = len(report.areas)
    rows = {r.area_id: r for r in report.areas}
    assert report.over_requesting_area_ids
    for area_id in report.over_requesting_area_ids:
        row = rows[area_id]
        assert row.vulnerability_index < LOW_VULNERABILITY
        assert row.distance_rank > n - RANK_K
        assert row.request_rank > n - RANK_K
        assert row.requested_litres > row.predicted_shortfall_litres


@pytest.mark.parametrize("scenario_id", ["scn_sa01_w06", "scn_sa01_w06_severe"])
def test_shortfall_exceeds_supply_by_the_demo_ratio(reports, scenario_id):
    report = reports[scenario_id]
    assert report.total_predicted_shortfall_litres >= MIN_SHORTFALL_TO_SUPPLY * report.supply_litres
    assert report.ratio_ok


def test_seed_ratios_are_ordered_by_severity(reports):
    ratios = {sid: r.shortfall_to_supply for sid, r in reports.items()}
    assert ratios["scn_sa01_w06_severe"] > ratios["scn_sa01_w06"] > ratios["scn_sa01_w06_mild"] > 1.0
