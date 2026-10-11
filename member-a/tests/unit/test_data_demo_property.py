"""MEMBER_A F1 demo property on scn_sa01_w06: FCFS and nearest-first starve far, vulnerable, late areas.

The model is trained separately, so "shortfall" is computed from the hidden truth. Margins make the
property survive a model whose predicted needs are off by up to +-12 %.
"""
from __future__ import annotations

import pytest

from jp_a.data.debt import debt_by_area
from jp_a.domain.entities import Scenario

from .data_helpers import (
    MAIN_ID,
    SEED_IDS,
    earliest_requests,
    farthest,
    latest_requests,
    load_seed,
    load_truth,
    nearest,
    true_shortfalls,
)

NEED_ERROR_SCALES = (0.88, 1.0, 1.12)


def demo_areas(scenario: Scenario, truth: dict, need_scale: float = 1.0, request_margin: float = 1.0) -> list[str]:
    """Areas meeting every F1 condition; ``requested < request_margin * shortfall``."""
    shortfall = true_shortfalls(scenario, truth, need_scale)
    debt = debt_by_area(scenario)
    far, late = farthest(scenario), latest_requests(scenario)
    return sorted(
        a.area_id
        for a in scenario.areas
        if a.vulnerability_index >= 0.6
        and a.area_id in far
        and a.area_id in late
        and a.requested_litres < request_margin * shortfall[a.area_id]
        and debt[a.area_id] > 0
    )


def loud_near_areas(scenario: Scenario, truth: dict, need_scale: float = 1.0, request_margin: float = 1.0) -> list[str]:
    """Near, early, low-vulnerability areas with ``requested > request_margin * shortfall``."""
    shortfall = true_shortfalls(scenario, truth, need_scale)
    near, early = nearest(scenario), earliest_requests(scenario)
    return sorted(
        a.area_id
        for a in scenario.areas
        if a.area_id in near
        and a.area_id in early
        and a.vulnerability_index < 0.4
        and a.requested_litres > request_margin * shortfall[a.area_id]
    )


@pytest.fixture(scope="module")
def main() -> tuple[Scenario, dict]:
    return load_seed(MAIN_ID), load_truth(MAIN_ID)


def test_at_least_two_demo_areas(main):
    assert len(demo_areas(*main)) >= 2


def test_demo_areas_requests_are_clearly_below_shortfall(main):
    """requested_litres at least 25 % below the true shortfall."""
    assert len(demo_areas(*main, request_margin=0.75)) >= 2


def test_demo_areas_are_clearly_vulnerable_and_indebted(main):
    scenario, truth = main
    areas = {a.area_id: a for a in scenario.areas}
    debt = debt_by_area(scenario)
    strong = [aid for aid in demo_areas(scenario, truth) if areas[aid].vulnerability_index >= 0.7 and debt[aid] >= 5000]
    assert len(strong) >= 2


@pytest.mark.parametrize("need_scale", NEED_ERROR_SCALES)
def test_demo_property_survives_need_errors(main, need_scale):
    assert len(demo_areas(*main, need_scale=need_scale)) >= 2
    assert loud_near_areas(*main, need_scale=need_scale)


def test_total_shortfall_exceeds_supply_with_margin(main):
    scenario, truth = main
    ratio = sum(true_shortfalls(scenario, truth).values()) / scenario.supply.tanker_water_available_litres
    assert ratio >= 1.75  # requirement: >= 1.6


def test_loud_near_area_is_over_requesting(main):
    assert loud_near_areas(*main, request_margin=1.2)


def test_far_areas_were_starved_by_nearest_first_history(main):
    scenario, _ = main
    debt = debt_by_area(scenario)
    far, near = farthest(scenario), nearest(scenario)
    assert min(debt[a] for a in far) > max(debt[a] for a in near)


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_area_level_property_holds_on_every_seed(scenario_id):
    scenario, truth = load_seed(scenario_id), load_truth(scenario_id)
    assert len(demo_areas(scenario, truth)) >= 2
    assert loud_near_areas(scenario, truth)
