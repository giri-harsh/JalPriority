"""Route 6.6 POST /a/v1/plans/solve end to end, with a Container built from in-memory fakes."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from jp_a.api.deps import Container
from jp_a.api.errors import ModelUnavailableError, ScenarioNotFoundError
from jp_a.domain.entities import ErrorEnvelope, PlanResult, PredictionSet, Scenario
from jp_a.main import create_app
from jp_a.optimization import solve as solve_module
from jp_a.services.plan_service import PlanService
from jp_a.services.scenario_service import ScenarioService

from .opt_helpers import golden_inputs, load_fixture, stable_dump

SOLVE_URL = "/a/v1/plans/solve"


class FakeScenarioRepo:
    """The scenario-repository surface PlanService uses, backed by a dict."""

    def __init__(self, scenarios: list[Scenario]):
        self._scenarios = {s.scenario_id: s for s in scenarios}

    def get(self, scenario_id: str) -> Scenario:
        if scenario_id not in self._scenarios:
            raise ScenarioNotFoundError(scenario_id)
        return self._scenarios[scenario_id]

    def exists(self, scenario_id: str) -> bool:
        return scenario_id in self._scenarios


class FakePredictionService:
    """Returns the fixture PredictionSet for the contract scenario; can simulate a missing model."""

    def __init__(self, prediction: PredictionSet, *, available: bool = True):
        self.prediction = prediction
        self.available = available
        self.calls: list[str] = []

    def predict_for_scenario(self, scenario: Scenario) -> PredictionSet:
        self.calls.append(scenario.scenario_id)
        if not self.available:
            raise ModelUnavailableError("Model artefact is not loaded.")
        return self.prediction


class FakeModelRepo:
    def load(self) -> None:
        """Nothing to load."""


def _client(settings, *, model_available: bool = True) -> tuple[TestClient, FakePredictionService]:
    scenario, prediction, _, _ = golden_inputs()
    repo = FakeScenarioRepo([scenario])
    predictions = FakePredictionService(prediction, available=model_available)
    container = Container(
        settings=settings,
        scenario_repo=repo,
        model_repo=FakeModelRepo(),
        prediction_service=predictions,
        scenario_service=ScenarioService(settings, repo),
        plan_service=PlanService(settings, repo, predictions),
    )
    return TestClient(create_app(settings, container)), predictions


@pytest.fixture()
def api(settings):
    client, predictions = _client(settings)
    with client:
        yield client, predictions


def _request(**overrides) -> dict:
    return {**load_fixture("contract_solve_request_example"), **overrides}


def _error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = ErrorEnvelope.model_validate(response.json())
    assert body.error.code == code
    assert body.error.request_id == response.headers["X-Request-ID"]
    return body.error.details


def test_solve_returns_the_contract_plan(api, auth_headers):
    client, predictions = api
    started = time.perf_counter()
    response = client.post(SOLVE_URL, json=_request(), headers=auth_headers)
    assert time.perf_counter() - started < 8.0 + 1.0
    assert response.status_code == 200, response.text
    plan = PlanResult.model_validate(response.json())  # strict parse of the live response
    expected = load_fixture("contract_plan_result_example")
    expected.pop("result_id")
    expected.pop("generated_at")
    expected["solver"].pop("solve_time_ms")
    assert stable_dump(plan) == expected
    assert set(response.json()) == set(load_fixture("contract_plan_result_example"))
    assert predictions.calls == ["scn_sa01_w06"]


def test_time_limit_defaults_to_settings(settings, auth_headers):
    tuned = settings.model_copy(update={"a_solver_time_limit_s": 5.0})
    client, _ = _client(tuned)
    body = _request()
    body.pop("time_limit_s")
    with client:
        response = client.post(SOLVE_URL, json=body, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["solver"]["time_limit_s"] == 5.0


def test_short_time_limit_still_returns_a_plan(api, auth_headers):
    client, _ = api
    started = time.perf_counter()
    response = client.post(SOLVE_URL, json=_request(time_limit_s=0.5), headers=auth_headers)
    assert time.perf_counter() - started < 1.5
    plan = PlanResult.model_validate(response.json())
    assert plan.solver.time_limit_s == 0.5
    assert plan.constraint_check.violations == []


def test_missing_debt_entries_count_as_zero(api, auth_headers):
    client, _ = api
    response = client.post(SOLVE_URL, json=_request(debt_by_area={"ar_03": 12_000}), headers=auth_headers)
    assert response.status_code == 200
    allocations = {a["area_id"]: a for a in response.json()["allocations"]}
    assert allocations["ar_02"]["debt_litres_used"] == 0 and allocations["ar_02"]["debt_normalized"] == 0.0


def test_unknown_scenario_is_404(api, auth_headers):
    client, _ = api
    details = _error(client.post(SOLVE_URL, json=_request(scenario_id="scn_sa01_w99"), headers=auth_headers), 404, "SCENARIO_NOT_FOUND")
    assert details == {"scenario_id": "scn_sa01_w99"}


def test_unknown_debt_area_is_422(api, auth_headers):
    client, predictions = api
    body = _request(debt_by_area={"ar_01": 0, "ar_09": 500, "ar_07": 1})
    details = _error(client.post(SOLVE_URL, json=body, headers=auth_headers), 422, "VALIDATION_ERROR")
    assert details == {"unknown_area_ids": ["ar_07", "ar_09"]}
    assert predictions.calls == []  # rejected before any prediction work


@pytest.mark.parametrize(
    "overrides",
    [
        {"weights": {"urgency": 6.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}},
        {"weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": -1.0}},
        {"weights": None},
        {"debt_by_area": None},
        {"debt_by_area": {"ar_01": -1}},
        {"time_limit_s": 0.1},
        {"time_limit_s": 12.0},
        {"scenario_id": "not a scenario"},
    ],
)
def test_invalid_requests_are_422(api, auth_headers, overrides):
    client, _ = api
    details = _error(client.post(SOLVE_URL, json=_request(**overrides), headers=auth_headers), 422, "VALIDATION_ERROR")
    assert details["errors"]


def test_missing_or_bad_token_is_401(api, make_token):
    client, _ = api
    _error(client.post(SOLVE_URL, json=_request()), 401, "UNAUTHORIZED")
    bad_issuer = {"Authorization": f"Bearer {make_token(iss='someone-else')}"}
    _error(client.post(SOLVE_URL, json=_request(), headers=bad_issuer), 401, "UNAUTHORIZED")
    expired = {"Authorization": f"Bearer {make_token(ttl=-120)}"}
    _error(client.post(SOLVE_URL, json=_request(), headers=expired), 401, "UNAUTHORIZED")


def test_model_unavailable_is_503(settings, auth_headers):
    client, _ = _client(settings, model_available=False)
    with client:
        _error(client.post(SOLVE_URL, json=_request(), headers=auth_headers), 503, "MODEL_UNAVAILABLE")


def test_solver_crash_is_500(api, auth_headers, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(solve_module, "solve_cpsat", broken)
    monkeypatch.setattr(solve_module, "solve_greedy", broken)
    client, _ = api
    details = _error(client.post(SOLVE_URL, json=_request(), headers=auth_headers), 500, "SOLVER_ERROR")
    assert details == {"scenario_id": "scn_sa01_w06"}


def test_cp_sat_failure_alone_degrades_to_the_greedy_plan(api, auth_headers, monkeypatch):
    monkeypatch.setattr(solve_module, "solve_cpsat", lambda *a, **k: None)
    client, _ = api
    response = client.post(SOLVE_URL, json=_request(), headers=auth_headers)
    assert response.status_code == 200
    plan = PlanResult.model_validate(response.json())
    assert (plan.solver.engine, plan.solver.status) == ("greedy-fallback", "heuristic")
    assert all(a.reasons[-1].code == "TIME_LIMITED_SOLUTION" for a in plan.allocations)
