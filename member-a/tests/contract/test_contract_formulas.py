"""MEMBER_A section 6 item 3: CONTRACT 5.11 formulas on live solve responses.

F-1..F-8 are recomputed independently here (no production formula code is imported) from the Scenario and the
PredictionSet served by the real API, and compared with every field of the solve response at the contract rounding.
F-15 must be 0 and ``constraint_check.violations`` must be empty.
"""
from __future__ import annotations

import copy
import random

import pytest
from fastapi.testclient import TestClient

from jp_a.main import create_app

from .contract_helpers import (
    API,
    SEEDS,
    assert_formulas,
    bearer,
    copy_real_data,
    count_violations,
    expected_plan,
    fake_container,
    path_km,
    real_settings,
)

AREA_IDS = [f"ar_{n:02d}" for n in range(1, 9)]


def _random_debt(seed: int, density: float = 0.7, top: int = 20_000) -> dict[str, int]:
    rng = random.Random(seed)
    return {a: rng.randrange(0, top, 100) for a in AREA_IDS if rng.random() < density}


# (label, weights, debt_by_area)
VARIANTS = [
    ("contract-defaults", {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}, {"ar_01": 0, "ar_02": 8000, "ar_03": 12000}),
    ("no-debt", {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}, {}),
    ("urgency-only", {"urgency": 5.0, "debt": 0.0, "vulnerability": 0.0, "travel": 0.0}, _random_debt(1)),
    ("debt-heavy-far-averse", {"urgency": 0.0, "debt": 5.0, "vulnerability": 0.0, "travel": 500.0}, _random_debt(2)),
    ("vulnerability-heavy", {"urgency": 0.3, "debt": 0.2, "vulnerability": 5.0, "travel": 100.0}, _random_debt(3, density=0.4)),
    ("all-weights-zero", {"urgency": 0.0, "debt": 0.0, "vulnerability": 0.0, "travel": 0.0}, _random_debt(4)),
    ("equal-debts", {"urgency": 2.5, "debt": 2.5, "vulnerability": 2.5, "travel": 250.0}, {a: 5000 for a in AREA_IDS}),
    ("one-huge-debt", {"urgency": 1.0, "debt": 3.0, "vulnerability": 1.0, "travel": 20.0}, {"ar_05": 123_400}),
    ("fractional-weights", {"urgency": 0.37, "debt": 1.73, "vulnerability": 0.91, "travel": 7.5}, _random_debt(5, density=1.0, top=9_000)),
]


@pytest.fixture(scope="module")
def api(settings, tmp_path_factory):
    data = copy_real_data(tmp_path_factory.mktemp("contract_formulas_data"))
    with TestClient(create_app(real_settings(settings, data))) as c:
        yield c


@pytest.fixture(scope="module")
def hdr(make_token):
    return bearer(make_token())


@pytest.fixture(scope="module")
def world(api, hdr):
    """Scenario + PredictionSet for each seed, exactly as the API serves them."""
    out = {}
    for sid in SEEDS:
        sc = api.get(f"{API}/scenarios/{sid}", headers=hdr)
        pr = api.get(f"{API}/scenarios/{sid}/predictions", headers=hdr)
        assert sc.status_code == pr.status_code == 200
        out[sid] = (sc.json(), pr.json())
    return out


@pytest.mark.parametrize("label,weights,debt", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("sid", SEEDS)
def test_solve_response_matches_independent_formulas(api, hdr, world, sid, label, weights, debt):
    scenario, prediction = world[sid]
    r = api.post(
        f"{API}/plans/solve",
        json={"scenario_id": sid, "weights": weights, "debt_by_area": debt, "time_limit_s": 2.0},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["weights"] == weights
    assert plan["solver"]["status"] in {"optimal", "feasible", "time_limited", "heuristic"}
    assert_formulas(scenario, prediction, weights, debt, plan)
    assert plan["prediction"]["model_version"] == prediction["model_version"]
    assert plan["prediction"]["run_mae_litres"] == prediction["run_mae_litres"]


@pytest.mark.parametrize("sid", SEEDS)
def test_prediction_shortfalls_follow_f1(world, sid):
    scenario, prediction = world[sid]
    piped = {a["area_id"]: a["expected_piped_litres"] for a in scenario["areas"]}
    for p in prediction["areas"]:
        assert p["expected_piped_litres"] == piped[p["area_id"]]
        assert p["predicted_shortfall_litres"] == max(0, p["predicted_need_litres"] - p["expected_piped_litres"])
        assert isinstance(p["predicted_shortfall_litres"], int)


@pytest.mark.parametrize("sid", SEEDS)
def test_debt_is_normalised_by_the_scenario_maximum(api, hdr, world, sid):
    """F-3: D_hat = D / max(1, max_j D_j) over ALL areas, including areas absent from the request."""
    scenario, prediction = world[sid]
    debt = {"ar_02": 4000, "ar_07": 16000}
    r = api.post(
        f"{API}/plans/solve",
        json={"scenario_id": sid, "weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}, "debt_by_area": debt, "time_limit_s": 2.0},
        headers=hdr,
    )
    by_area = {a["area_id"]: a for a in r.json()["allocations"]}
    assert by_area["ar_07"]["debt_normalized"] == 1.0
    assert by_area["ar_02"]["debt_normalized"] == 0.25
    assert by_area["ar_01"]["debt_normalized"] == 0.0 and by_area["ar_01"]["debt_litres_used"] == 0
    assert {a: by_area[a]["debt_litres_used"] for a in debt} == debt


@pytest.mark.parametrize("sid", SEEDS)
def test_all_zero_debt_normalises_to_zero(api, hdr, world, sid):
    """F-3 with max(1, max D): an all-zero vector must give D_hat = 0, not a division by zero."""
    r = api.post(
        f"{API}/plans/solve",
        json={"scenario_id": sid, "weights": {"urgency": 1.0, "debt": 5.0, "vulnerability": 0.5, "travel": 20.0}, "debt_by_area": {a: 0 for a in AREA_IDS}, "time_limit_s": 2.0},
        headers=hdr,
    )
    assert r.status_code == 200
    assert all(a["debt_normalized"] == 0.0 for a in r.json()["allocations"])


def test_worked_check_of_section_5_11(load_fixture):
    """The independent recomputation reproduces the CONTRACT worked check (1.4093 / 2.2144 / 2.6857)."""
    scenario = load_fixture("contract_scenario_example")
    prediction = load_fixture("contract_prediction_example")
    request = load_fixture("contract_solve_request_example")
    exp = expected_plan(scenario, prediction, request["weights"], request["debt_by_area"])
    assert {a: v["priority_weight"] for a, v in exp["areas"].items()} == {"ar_01": 1.4093, "ar_02": 2.2144, "ar_03": 2.6857}
    assert {a: v["debt_normalized"] for a, v in exp["areas"].items()} == {"ar_01": 0.0, "ar_02": 0.6667, "ar_03": 1.0}
    assert exp["order"] == ["ar_03", "ar_02", "ar_01"]


def test_path_km_and_violation_counter_agree_with_the_golden_fixture(load_fixture):
    """Self-test of the independent helpers against CONTRACT 5.6 (F-8, F-14, F-15)."""
    scenario = load_fixture("contract_scenario_example")
    plan = load_fixture("contract_plan_result_example")
    assert [path_km(scenario["distance_km"], r["path"]) for r in plan["routes"]] == [24.4, 12.8, 0.0]
    assert round(sum(r["route_km"] for r in plan["routes"]), 2) == plan["totals"]["travel_km"] == 37.2
    assert count_violations(scenario, plan) == 0

    broken = copy.deepcopy(plan)
    broken["allocations"][0]["deliveries"][0]["litres"] = 13_000  # tk_01 load 13 000 + 5 000 > capacity 12 000
    assert count_violations(scenario, broken) >= 2  # supply cap exceeded + tanker over capacity
    used_unavailable = copy.deepcopy(plan)
    used_unavailable["allocations"][0]["deliveries"][0]["tanker_id"] = "tk_03"
    assert count_violations(scenario, used_unavailable) >= 1
    too_far = copy.deepcopy(plan)
    too_far["routes"][0]["path"] = ["dp_01", "ar_03", "ar_01", "ar_02"] * 6 + ["dp_01"]
    assert count_violations(scenario, too_far) >= 1


def _mutate(plan, path, value):
    broken = copy.deepcopy(plan)
    target = broken
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return broken


@pytest.mark.parametrize(
    "path,value",
    [
        (("allocations", 0, "priority_weight"), 2.6858),
        (("allocations", 1, "urgency"), 0.2728),
        (("allocations", 1, "debt_normalized"), 0.6666),
        (("allocations", 0, "vulnerability_used"), 0.79),
        (("allocations", 2, "coverage_ratio"), 0.357),
        (("allocations", 2, "unmet_litres"), 8900),
        (("allocations", 2, "predicted_shortfall_litres"), 14100),
        (("routes", 0, "route_km"), 24.41),
        (("totals", "travel_km"), 37.3),
        (("totals", "unmet_litres"), 9100),
        (("totals", "shortfall_litres"), 29100),
        (("totals", "tanker_capacity_available_litres"), 32000),
        (("constraint_check", "water_cap_ok"), False),
    ],
)
def test_formula_checker_detects_a_tampered_plan(load_fixture, path, value):
    """Guards the checker itself: a single wrong field in an otherwise correct plan must fail assert_formulas."""
    scenario = load_fixture("contract_scenario_example")
    prediction = load_fixture("contract_prediction_example")
    request = load_fixture("contract_solve_request_example")
    plan = load_fixture("contract_plan_result_example")
    assert_formulas(scenario, prediction, request["weights"], request["debt_by_area"], plan)
    with pytest.raises(AssertionError):
        assert_formulas(scenario, prediction, request["weights"], request["debt_by_area"], _mutate(plan, path, value))


# --------------------------------------------------------------------------- edge cases through a fake container
@pytest.fixture()
def edge_world(load_fixture):
    scenario = load_fixture("contract_scenario_example")
    prediction = load_fixture("contract_prediction_example")
    return scenario, prediction


def _solve_fake(settings, make_token, scenario, prediction, weights, debt):
    with TestClient(create_app(settings, fake_container(settings, scenario, prediction))) as c:
        r = c.post(
            f"{API}/plans/solve",
            json={"scenario_id": scenario["scenario_id"], "weights": weights, "debt_by_area": debt, "time_limit_s": 2.0},
            headers=bearer(make_token()),
        )
    assert r.status_code == 200, r.text
    return r.json()


def test_no_shortfall_and_unavailable_vulnerability_edge_cases(settings, make_token, edge_world):
    """F-1 clamps at 0 (need below piped), F-2 is 0 for need 0, F-4 is 0 when vulnerability is unavailable."""
    scenario, prediction = copy.deepcopy(edge_world[0]), copy.deepcopy(edge_world[1])
    by_area = {p["area_id"]: p for p in prediction["areas"]}
    by_area["ar_01"]["predicted_need_litres"] = 30_000  # < expected piped 40 000 -> shortfall 0
    by_area["ar_01"]["predicted_shortfall_litres"] = 0
    by_area["ar_03"]["predicted_need_litres"] = 0  # need 0 -> urgency 0, shortfall 0
    by_area["ar_03"]["predicted_shortfall_litres"] = 0
    next(a for a in scenario["areas"] if a["area_id"] == "ar_02")["vulnerability_available"] = False
    weights = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
    debt = {"ar_01": 3000, "ar_02": 8000, "ar_03": 12000}

    plan = _solve_fake(settings, make_token, scenario, prediction, weights, debt)
    assert_formulas(scenario, prediction, weights, debt, plan)
    allocs = {a["area_id"]: a for a in plan["allocations"]}
    assert allocs["ar_01"]["predicted_shortfall_litres"] == 0 and allocs["ar_01"]["coverage_ratio"] == 1.0
    assert allocs["ar_01"]["allocated_litres"] == 0 and allocs["ar_01"]["unmet_litres"] == 0
    assert allocs["ar_03"]["urgency"] == 0.0 and allocs["ar_03"]["coverage_ratio"] == 1.0
    assert allocs["ar_02"]["vulnerability_used"] == 0.0
    assert plan["totals"]["shortfall_litres"] == 9000
    assert plan["unserved"] == []


def test_zero_total_shortfall_plan_is_empty_but_valid(settings, make_token, edge_world):
    scenario, prediction = copy.deepcopy(edge_world[0]), copy.deepcopy(edge_world[1])
    for p in prediction["areas"]:
        p["predicted_need_litres"] = p["expected_piped_litres"] - 1000
        p["predicted_shortfall_litres"] = 0
    weights = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
    plan = _solve_fake(settings, make_token, scenario, prediction, weights, {"ar_02": 100})
    assert_formulas(scenario, prediction, weights, {"ar_02": 100}, plan)
    assert plan["totals"]["allocated_litres"] == 0 and plan["totals"]["travel_km"] == 0.0
    assert all(r["status"] in {"idle", "unavailable"} for r in plan["routes"])
    assert all("NO_SHORTFALL" in [x["code"] for x in a["reasons"]] for a in plan["allocations"])
