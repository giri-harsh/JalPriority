"""End-to-end flow through the real app (real data copy + committed model + minted token).

list scenarios -> predictions for each seed -> solve each seed -> disruptions -> re-solve.
The plan checker is the independent recomputation from ``tests/property/property_helpers.py``.
"""
from __future__ import annotations

import re
import time

import pytest

from jp_a.domain.entities import (
    DERIVED_SCENARIO_RE,
    PlanResult,
    PredictionSet,
    Scenario,
    ScenarioList,
    Weights,
)

from ..property.property_helpers import count_violations, plan_problems
from .e2e_helpers import (  # noqa: F401  (fixtures)
    DEFAULT_WEIGHTS,
    MAIN_ID,
    SEED_IDS,
    WIDE_ID,
    api,
    client_a,
    data_dir,
    e2e_settings,
    expected_supply_after_cut,
    headers,
    solve_body,
)

SOLVE_LIMIT_S = 3.0
REPLAN_BUDGET_S = SOLVE_LIMIT_S + 1.0


def _solve(client_a, scenario: Scenario, **kwargs) -> tuple[PlanResult, float]:
    started = time.perf_counter()
    r = client_a.solve(solve_body(scenario, time_limit_s=SOLVE_LIMIT_S, **kwargs))
    elapsed = time.perf_counter() - started
    assert r.status_code == 200, r.text
    return PlanResult.model_validate(r.json()), elapsed


# --------------------------------------------------------------------------- auth + listing
def test_every_route_requires_the_token(api):
    for method, path in (
        ("get", "/a/v1/scenarios"),
        ("get", f"/a/v1/scenarios/{MAIN_ID}"),
        ("get", f"/a/v1/scenarios/{MAIN_ID}/predictions"),
        ("get", "/a/v1/model"),
    ):
        r = getattr(api, method)(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert api.post("/a/v1/plans/solve", json={}).status_code == 401


def test_health_is_up_with_the_model_loaded(api):
    r = api.get("/a/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["model_version"]


def test_list_scenarios_returns_the_three_seeds(client_a):
    r = client_a.http.get("/a/v1/scenarios", headers=client_a.headers)
    assert r.status_code == 200
    listing = ScenarioList.model_validate(r.json())
    ids = {s.scenario_id for s in listing.scenarios}
    assert set(SEED_IDS) <= ids
    by_id = {s.scenario_id: s for s in listing.scenarios}
    for sid in SEED_IDS:
        summary = by_id[sid]
        assert summary.n_areas == 8
        assert summary.tankers_available == 4
        assert summary.parent_scenario_id is None and summary.is_synthetic
    assert [by_id[s].alert_level for s in (MAIN_ID, "scn_sa01_w06_severe", "scn_sa01_w06_mild")] == [2, 3, 1]
    assert [s.scenario_id for s in listing.scenarios] == sorted(ids)


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_each_seed_scenario_is_served_in_full(client_a, scenario_id):
    scenario = client_a.scenario(scenario_id)
    assert scenario.scenario_id == scenario_id and scenario.is_synthetic
    assert len(scenario.areas) == 8 and len(scenario.tankers) == 4
    assert all(len(a.history) == 5 for a in scenario.areas)
    assert scenario.window_index == 6


# --------------------------------------------------------------------------- predictions
@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_predictions_for_each_seed(client_a, scenario_id):
    scenario = client_a.scenario(scenario_id)
    started = time.perf_counter()
    prediction = client_a.predictions(scenario_id)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0
    assert prediction.latency_ms < 2000 and prediction.is_simulation
    assert [p.area_id for p in prediction.areas] == [a.area_id for a in scenario.areas]
    piped = {a.area_id: a.expected_piped_litres for a in scenario.areas}
    for p in prediction.areas:
        assert p.expected_piped_litres == piped[p.area_id]
        assert p.predicted_shortfall_litres == max(0, p.predicted_need_litres - p.expected_piped_litres)  # F-1
        assert len(p.top_features) == 3
        assert len({f.feature for f in p.top_features}) == 3
        mags = [abs(f.contribution_litres) for f in p.top_features]
        assert mags == sorted(mags, reverse=True)
    assert prediction.run_mae_litres is not None and 0 < prediction.run_mae_litres < 5000


def test_predictions_are_cached_per_scenario(client_a):
    first = client_a.predictions(MAIN_ID).model_dump()
    second = client_a.predictions(MAIN_ID).model_dump()
    assert first == second


def test_unknown_scenario_is_404_everywhere(client_a):
    missing = "scn_does_not_exist"
    assert client_a.http.get(f"/a/v1/scenarios/{missing}", headers=client_a.headers).status_code == 404
    assert client_a.http.get(f"/a/v1/scenarios/{missing}/predictions", headers=client_a.headers).status_code == 404
    body = {"scenario_id": missing, "weights": DEFAULT_WEIGHTS, "debt_by_area": {}}
    r = client_a.solve(body)
    assert r.status_code == 404 and r.json()["error"]["code"] == "SCENARIO_NOT_FOUND"


# --------------------------------------------------------------------------- solve
@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_solve_each_seed_is_sound(client_a, scenario_id):
    scenario = client_a.scenario(scenario_id)
    prediction = client_a.predictions(scenario_id)
    plan, elapsed = _solve(client_a, scenario)
    assert elapsed < REPLAN_BUDGET_S
    weights = Weights(**DEFAULT_WEIGHTS)
    debt = solve_body(scenario)["debt_by_area"]
    assert plan_problems(plan, scenario, prediction, weights, debt) == []
    assert plan.strategy == "jalpriority" and plan.solver.engine == "ortools-cp-sat"
    assert count_violations(plan, scenario) == 0
    assert plan.prediction.model_version == prediction.model_version
    assert plan.prediction.run_mae_litres == prediction.run_mae_litres


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_water_is_the_binding_constraint_on_every_seed(client_a, scenario_id):
    """Supply < shortfall on all seeds, so the plan uses all the water and reports the unmet remainder."""
    scenario = client_a.scenario(scenario_id)
    plan, _ = _solve(client_a, scenario)
    assert plan.totals.shortfall_litres > scenario.supply.tanker_water_available_litres
    assert plan.totals.allocated_litres == scenario.supply.tanker_water_available_litres
    assert plan.unserved and {u.area_id for u in plan.unserved} <= {a.area_id for a in scenario.areas}


def test_solve_without_time_limit_uses_the_service_default(client_a):
    scenario = client_a.scenario(MAIN_ID)
    r = client_a.solve(solve_body(scenario))
    assert r.status_code == 200
    assert r.json()["solver"]["time_limit_s"] == 8.0


def test_solve_rejects_bad_weights_and_unknown_debt_areas(client_a):
    scenario = client_a.scenario(MAIN_ID)
    body = solve_body(scenario)
    bad = {**body, "weights": {**DEFAULT_WEIGHTS, "urgency": 9.0}}
    r = client_a.solve(bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client_a.solve({**body, "debt_by_area": {**body["debt_by_area"], "ar_99": 100}})
    assert r.status_code == 422


def test_weights_change_the_plan_but_never_its_validity(client_a):
    scenario = client_a.scenario(MAIN_ID)
    prediction = client_a.predictions(MAIN_ID)
    debt = solve_body(scenario)["debt_by_area"]
    seen = set()
    for weights in (
        {"urgency": 0.0, "debt": 0.0, "vulnerability": 0.0, "travel": 0.0},
        {"urgency": 5.0, "debt": 0.0, "vulnerability": 0.0, "travel": 0.0},
        {"urgency": 0.0, "debt": 5.0, "vulnerability": 5.0, "travel": 500.0},
        DEFAULT_WEIGHTS,
    ):
        plan, _ = _solve(client_a, scenario, weights=weights)
        assert plan_problems(plan, scenario, prediction, Weights(**weights), debt) == []
        seen.add(tuple(a.allocated_litres for a in sorted(plan.allocations, key=lambda a: a.area_id)))
    assert len(seen) >= 2  # the weights are not ignored


# --------------------------------------------------------------------------- disruptions -> re-solve
def _resolve_after(client_a, parent_id: str, dtype: str, params: dict) -> tuple[Scenario, PlanResult, PredictionSet]:
    r = client_a.disrupt(parent_id, dtype, params)
    assert r.status_code in (200, 201), r.text
    derived = Scenario.model_validate(r.json())
    assert DERIVED_SCENARIO_RE.match(derived.scenario_id)
    assert derived.parent_scenario_id == parent_id
    assert derived.disruptions_applied[-1].type == dtype
    assert client_a.scenario(derived.scenario_id) == derived  # persisted and served by GET
    prediction = client_a.predictions(derived.scenario_id)
    assert prediction.run_mae_litres is None  # derived scenarios have no hidden truth
    plan, elapsed = _solve(client_a, derived)
    assert elapsed < REPLAN_BUDGET_S
    debt = solve_body(derived)["debt_by_area"]
    assert plan_problems(plan, derived, prediction, Weights(**DEFAULT_WEIGHTS), debt) == []
    assert plan.scenario_id == derived.scenario_id
    return derived, plan, prediction


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_tanker_down_then_resolve(client_a, scenario_id):
    parent = client_a.scenario(scenario_id)
    base, _ = _solve(client_a, parent)
    busiest = max((r for r in base.routes if r.status == "assigned"), key=lambda r: r.load_litres)
    derived, plan, _ = _resolve_after(client_a, scenario_id, "tanker_down", {"tanker_id": busiest.tanker_id})
    assert next(t for t in derived.tankers if t.tanker_id == busiest.tanker_id).status == "unavailable"
    route = next(r for r in plan.routes if r.tanker_id == busiest.tanker_id)
    assert route.status == "unavailable" and route.stops == [] and route.load_litres == 0
    assert derived.supply == parent.supply and derived.heat == parent.heat
    # capacity shrank by one truck; the plan must respect it
    cap = sum(t.capacity_litres for t in derived.tankers if t.status == "available")
    assert plan.totals.tanker_capacity_available_litres == cap
    assert plan.totals.allocated_litres <= cap


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_supply_cut_then_resolve(client_a, scenario_id):
    parent = client_a.scenario(scenario_id)
    base, _ = _solve(client_a, parent)
    derived, plan, _ = _resolve_after(client_a, scenario_id, "supply_cut", {"fraction": 0.25})
    expected = expected_supply_after_cut(parent.supply.tanker_water_available_litres, 0.25)
    assert derived.supply.tanker_water_available_litres == expected
    assert plan.totals.supply_available_litres == expected
    assert plan.totals.allocated_litres <= expected
    assert plan.totals.allocated_litres < base.totals.allocated_litres
    assert plan.totals.unmet_litres > base.totals.unmet_litres


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_heat_alert_then_resolve(client_a, scenario_id):
    parent = client_a.scenario(scenario_id)
    new_level = 3 if parent.heat.alert_level != 3 else 1
    derived, plan, prediction = _resolve_after(client_a, scenario_id, "heat_alert", {"alert_level": new_level})
    assert derived.heat.alert_level == new_level
    assert derived.supply == parent.supply
    assert prediction.scenario_id == derived.scenario_id
    assert plan.totals.shortfall_litres == sum(p.predicted_shortfall_litres for p in prediction.areas)


def test_disruption_is_idempotent_and_chains(client_a):
    first = client_a.disrupt(MAIN_ID, "supply_cut", {"fraction": 0.25})
    again = client_a.disrupt(MAIN_ID, "supply_cut", {"fraction": 0.25})
    assert again.status_code == 200
    assert again.json()["scenario_id"] == first.json()["scenario_id"]
    chained = client_a.disrupt(first.json()["scenario_id"], "tanker_down", {"tanker_id": "tk_01"})
    assert chained.status_code in (200, 201)
    derived = Scenario.model_validate(chained.json())
    assert re.match(r"^scn_sa01_w06__d2_[0-9a-f]{6}$", derived.scenario_id)
    assert [d.type for d in derived.disruptions_applied] == ["supply_cut", "tanker_down"]
    prediction = client_a.predictions(derived.scenario_id)
    plan, _ = _solve(client_a, derived)
    debt = solve_body(derived)["debt_by_area"]
    assert plan_problems(plan, derived, prediction, Weights(**DEFAULT_WEIGHTS), debt) == []
    assert plan.totals.supply_available_litres == derived.supply.tanker_water_available_litres


def test_disruption_errors_use_the_contract_envelope(client_a):
    r = client_a.disrupt(MAIN_ID, "supply_cut", {"fraction": 0.99})
    assert r.status_code == 422 and r.json()["error"]["code"] == "DISRUPTION_INVALID"
    r = client_a.disrupt(MAIN_ID, "tanker_down", {"tanker_id": "tk_99"})
    assert r.status_code == 422
    r = client_a.disrupt(MAIN_ID, "heat_alert", {"alert_level": client_a.scenario(MAIN_ID).heat.alert_level})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DISRUPTION_NOT_APPLICABLE"
    r = client_a.disrupt("scn_does_not_exist", "supply_cut", {"fraction": 0.1})
    assert r.status_code == 404


def test_derived_scenarios_are_listed_only_on_request(client_a):
    client_a.disrupt(MAIN_ID, "supply_cut", {"fraction": 0.25})
    plain = ScenarioList.model_validate(
        client_a.http.get("/a/v1/scenarios", headers=client_a.headers).json()
    )
    with_derived = ScenarioList.model_validate(
        client_a.http.get("/a/v1/scenarios?include_derived=true", headers=client_a.headers).json()
    )
    assert all(s.parent_scenario_id is None for s in plain.scenarios)
    assert any(s.parent_scenario_id == MAIN_ID for s in with_derived.scenarios)


# --------------------------------------------------------------------------- the 10-area scenario
def test_ten_area_scenario_goes_through_the_whole_flow(client_a):
    scenario = client_a.scenario(WIDE_ID)
    assert len(scenario.areas) == 10
    prediction = client_a.predictions(WIDE_ID)
    assert isinstance(prediction, PredictionSet) and len(prediction.areas) == 10
    started = time.perf_counter()
    r = client_a.solve(solve_body(scenario))  # default time limit
    elapsed = time.perf_counter() - started
    assert r.status_code == 200
    assert elapsed < 10.0
    plan = PlanResult.model_validate(r.json())
    debt = solve_body(scenario)["debt_by_area"]
    assert plan_problems(plan, scenario, prediction, Weights(**DEFAULT_WEIGHTS), debt) == []
