"""The demo property and the demo moment, on the predictions and plans the REAL API serves.

Property (MEMBER_A F1, scn_sa01_w06): at least 2 far, late, vulnerable, indebted areas that request less than
their predicted shortfall, total predicted shortfall >= 1.6 x supply, and a near/early/low-vulnerability area
that over-requests.

Moment: FCFS (by request_time) and nearest-first (by depot distance), both serving the REQUESTED litres from
the same supply and ignoring tanker routing, starve those areas; JalPriority covers each of them clearly
better and its minimum coverage is not lower.
"""
from __future__ import annotations

import pytest

from jp_a.data.debt import debt_by_area
from jp_a.domain.entities import PlanResult

from .e2e_helpers import (
    LOW_VULNERABILITY,
    MAIN_ID,
    MIN_DEMO_AREAS,
    MIN_SHORTFALL_TO_SUPPLY,
    MIN_VULNERABILITY,
    RANK_K,
    SEED_IDS,
    SEVERE_ID,
    coverage,
    demo_areas,
    depot_km,
    early_requests,
    far_set,
    fcfs_allocation,
    late_requests,
    near_set,
    nearest_first_allocation,
    over_requesting_areas,
    shortfalls,
    solve_body,
    wasted_litres,
)

STARVED_MAX_COVERAGE = 0.05  # "~0" under the baselines
CLEAR_GAIN = 0.20  # JalPriority covers each demo area at least this much better (absolute coverage)


@pytest.fixture(scope="module")
def world(client_a):
    """scenario + real predictions + the plan JalPriority returns over HTTP, for every seed."""
    out = {}
    for sid in SEED_IDS:
        scenario = client_a.scenario(sid)
        prediction = client_a.predictions(sid)
        r = client_a.solve(solve_body(scenario, time_limit_s=3.0))
        assert r.status_code == 200, r.text
        out[sid] = (scenario, prediction, PlanResult.model_validate(r.json()))
    return out


def _jp_coverage(plan: PlanResult) -> dict[str, float]:
    return {a.area_id: a.coverage_ratio for a in plan.allocations}


# --------------------------------------------------------------------------- the property
def test_at_least_two_far_late_vulnerable_underrequesting_areas(world):
    scenario, prediction, _ = world[MAIN_ID]
    chosen = demo_areas(scenario, prediction)
    assert len(chosen) >= MIN_DEMO_AREAS, chosen
    by_id = {a.area_id: a for a in scenario.areas}
    sf = shortfalls(prediction)
    debt = debt_by_area(scenario)
    far, late = far_set(scenario), late_requests(scenario)
    for area_id in chosen:
        area = by_id[area_id]
        assert area.vulnerability_index >= MIN_VULNERABILITY
        assert area_id in far and area_id in late  # among the 3 farthest AND the 3 latest requests
        assert area.requested_litres < sf[area_id]
        assert debt[area_id] > 0  # debt after window 5, F-10 with lambda = 0.3


def test_demo_areas_are_far_by_road_distance_and_late_by_request_time(world):
    scenario, prediction, _ = world[MAIN_ID]
    dist = depot_km(scenario)
    ranked_far = sorted(dist, key=lambda a: -dist[a])[:RANK_K]
    ranked_late = [a.area_id for a in sorted(scenario.areas, key=lambda a: a.request_time)][-RANK_K:]
    for area_id in demo_areas(scenario, prediction):
        assert area_id in ranked_far
        assert area_id in ranked_late


def test_total_predicted_shortfall_is_at_least_1_6_times_supply(world):
    scenario, prediction, plan = world[MAIN_ID]
    supply = scenario.supply.tanker_water_available_litres
    total = sum(shortfalls(prediction).values())
    assert total >= MIN_SHORTFALL_TO_SUPPLY * supply, f"{total} vs {supply}"
    assert plan.totals.shortfall_litres == total
    assert plan.totals.supply_available_litres == supply


def test_a_near_early_low_vulnerability_area_over_requests(world):
    scenario, prediction, _ = world[MAIN_ID]
    loud = over_requesting_areas(scenario, prediction)
    assert loud, "no near/early/low-vulnerability area requests more than its predicted shortfall"
    by_id = {a.area_id: a for a in scenario.areas}
    sf = shortfalls(prediction)
    for area_id in loud:
        assert by_id[area_id].vulnerability_index < LOW_VULNERABILITY
        assert area_id in near_set(scenario) and area_id in early_requests(scenario)
        assert by_id[area_id].requested_litres > sf[area_id]


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_area_level_property_holds_on_every_seed(world, scenario_id):
    scenario, prediction, _ = world[scenario_id]
    assert len(demo_areas(scenario, prediction)) >= MIN_DEMO_AREAS
    assert over_requesting_areas(scenario, prediction)


def test_severe_seed_is_scarcer_than_the_main_seed(world):
    ratios = {
        sid: sum(shortfalls(world[sid][1]).values()) / world[sid][0].supply.tanker_water_available_litres
        for sid in SEED_IDS
    }
    assert ratios[SEVERE_ID] > ratios[MAIN_ID] > ratios["scn_sa01_w06_mild"] > 1.0


# --------------------------------------------------------------------------- the baselines themselves
@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_baselines_spend_exactly_the_same_supply_on_requests(world, scenario_id):
    scenario, _, _ = world[scenario_id]
    supply = scenario.supply.tanker_water_available_litres
    requested = {a.area_id: a.requested_litres for a in scenario.areas}
    assert sum(requested.values()) > supply  # otherwise the order would not matter
    for allocation in (fcfs_allocation(scenario), nearest_first_allocation(scenario)):
        assert sum(allocation.values()) == supply
        assert all(0 <= allocation[a] <= requested[a] for a in requested)


def test_fcfs_serves_in_request_order_and_nearest_first_in_distance_order(world):
    scenario, _, _ = world[MAIN_ID]
    requested = {a.area_id: a.requested_litres for a in scenario.areas}
    fcfs, nearest = fcfs_allocation(scenario), nearest_first_allocation(scenario)

    def served_prefix(allocation: dict[str, int], order: list[str]) -> bool:
        """Everything before the first partially served area is fully served; everything after gets 0."""
        state = "full"
        for area_id in order:
            if state == "full":
                if allocation[area_id] < requested[area_id]:
                    state = "empty"
            elif allocation[area_id] != 0:
                return False
        return True

    by_time = [a.area_id for a in sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id))]
    dist = depot_km(scenario)
    by_distance = sorted(dist, key=lambda a: (dist[a], a))
    assert served_prefix(fcfs, by_time)
    assert served_prefix(nearest, by_distance)


# --------------------------------------------------------------------------- the demo moment
@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_baselines_starve_the_far_vulnerable_late_areas(world, scenario_id):
    scenario, prediction, _ = world[scenario_id]
    sf = shortfalls(prediction)
    chosen = demo_areas(scenario, prediction)
    for name, allocation in (("fcfs", fcfs_allocation(scenario)), ("nearest_first", nearest_first_allocation(scenario))):
        cov = coverage(allocation, sf)
        for area_id in chosen:
            assert cov[area_id] <= STARVED_MAX_COVERAGE, f"{name}: {area_id} coverage {cov[area_id]:.2f}"


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_jalpriority_covers_each_starved_area_clearly_more(world, scenario_id):
    scenario, prediction, plan = world[scenario_id]
    sf = shortfalls(prediction)
    jp = _jp_coverage(plan)
    for name, allocation in (("fcfs", fcfs_allocation(scenario)), ("nearest_first", nearest_first_allocation(scenario))):
        base = coverage(allocation, sf)
        for area_id in demo_areas(scenario, prediction):
            assert jp[area_id] >= base[area_id] + CLEAR_GAIN, (
                f"{scenario_id} {area_id}: jalpriority {jp[area_id]:.2f} vs {name} {base[area_id]:.2f}"
            )


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_jalpriority_minimum_coverage_is_not_lower(world, scenario_id):
    scenario, prediction, plan = world[scenario_id]
    sf = shortfalls(prediction)
    jp_min = min(_jp_coverage(plan).values())
    for allocation in (fcfs_allocation(scenario), nearest_first_allocation(scenario)):
        assert jp_min >= min(coverage(allocation, sf).values())


def test_main_demo_moment_in_numbers(world):
    """The headline: baselines leave the far vulnerable areas at ~0 and fix a floor of 0; JalPriority does not."""
    scenario, prediction, plan = world[MAIN_ID]
    sf = shortfalls(prediction)
    chosen = demo_areas(scenario, prediction)
    jp = _jp_coverage(plan)
    jp_mean = sum(jp[a] for a in chosen) / len(chosen)
    for allocation in (fcfs_allocation(scenario), nearest_first_allocation(scenario)):
        cov = coverage(allocation, sf)
        base_mean = sum(cov[a] for a in chosen) / len(chosen)
        assert base_mean <= STARVED_MAX_COVERAGE
        assert jp_mean >= 0.6
        assert min(cov.values()) == 0.0  # somebody gets nothing under the baselines
    assert min(jp.values()) > 0.0  # nobody gets nothing under JalPriority
    assert min(jp.values()) >= 0.2  # the soft coverage floor (A_COVERAGE_FLOOR = 0.25) is honoured, give or take 100 L


def test_baselines_waste_water_on_over_requesters_jalpriority_does_not(world):
    scenario, prediction, plan = world[MAIN_ID]
    sf = shortfalls(prediction)
    loud = over_requesting_areas(scenario, prediction)
    for allocation in (fcfs_allocation(scenario), nearest_first_allocation(scenario)):
        assert wasted_litres(allocation, sf) > 0
        assert any(allocation[a] > sf[a] for a in loud)  # served beyond the predicted shortfall
    assert all(a.allocated_litres <= a.predicted_shortfall_litres for a in plan.allocations)


def test_jalpriority_follows_priority_not_arrival_or_distance(world):
    """On the main seed the two most vulnerable, most indebted areas are served almost completely."""
    scenario, prediction, plan = world[MAIN_ID]
    debt = debt_by_area(scenario)
    by_id = {a.area_id: a for a in plan.allocations}
    top_debt = sorted(debt, key=lambda a: -debt[a])[:2]
    for area_id in top_debt:
        assert by_id[area_id].coverage_ratio >= 0.9, (area_id, by_id[area_id].coverage_ratio)
        assert by_id[area_id].rank <= 3
    # the loud near areas are served last by rank, not first
    loud = over_requesting_areas(scenario, prediction)
    ranks = {a.area_id: a.rank for a in plan.allocations}
    assert all(ranks[a] > len(ranks) // 2 for a in loud)
