"""M3/M4: GET /a/v1/model and GET /a/v1/scenarios/{id}/predictions through the real app (TestClient)."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from jp_a.api.deps import Container
from jp_a.domain.entities import FEATURE_NAMES, ErrorEnvelope, ModelInfo, PredictionSet
from jp_a.main import create_app
from jp_a.repositories.model_repo import ModelRepository
from jp_a.services.prediction_service import PredictionService

from .ml_helpers import DERIVED_ID, FakeScenarioRepo, train_synthetic_model

REQ_ID = re.compile(r"^req_[0-9a-f]{12}$")
MODEL_INFO_KEYS = {
    "schema_version", "model_version", "trained_at", "train_rows", "test_rows", "target", "metrics",
    "feature_importances", "is_simulation", "disclaimer",
}  # fmt: skip
PREDICTION_KEYS = {
    "schema_version", "scenario_id", "model_version", "generated_at", "latency_ms", "run_mae_litres",
    "is_simulation", "areas",
}  # fmt: skip
AREA_KEYS = {
    "area_id", "predicted_need_litres", "expected_piped_litres", "predicted_shortfall_litres",
    "baseline_last_need_litres", "top_features",
}  # fmt: skip


class _Unused:
    """Placeholder for services these tests never reach."""


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    return train_synthetic_model(tmp_path_factory.mktemp("ml_api"))


def _client(settings, models_dir):
    s = settings.model_copy(update={"a_models_dir": models_dir})
    scenario_repo = FakeScenarioRepo()
    model_repo = ModelRepository(s)
    container = Container(
        settings=s,
        scenario_repo=scenario_repo,
        model_repo=model_repo,
        prediction_service=PredictionService(s, scenario_repo, model_repo),
        scenario_service=_Unused(),
        plan_service=_Unused(),
    )
    return TestClient(create_app(s, container))


@pytest.fixture()
def api(settings, trained):
    with _client(settings, trained[0]) as c:
        yield c


@pytest.fixture()
def api_no_model(settings, tmp_path):
    with _client(settings, tmp_path / "empty-models") as c:
        yield c


def assert_envelope(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    ErrorEnvelope.model_validate(body)
    assert body["error"]["code"] == code
    assert REQ_ID.match(body["error"]["request_id"])
    assert resp.headers["x-request-id"] == body["error"]["request_id"]
    return body["error"]


# --------------------------------------------------------------------------- 6.7 GET /model
def test_model_info_200_strict(api, auth_headers, trained):
    r = api.get("/a/v1/model", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == MODEL_INFO_KEYS
    info = ModelInfo.model_validate(body)
    meta = trained[1]
    assert info.metrics.model_dump() == meta["metrics"]
    assert info.train_rows == meta["train_rows"] and info.test_rows == meta["test_rows"]
    assert [f.feature for f in info.feature_importances] and {f.feature for f in info.feature_importances} == set(FEATURE_NAMES)
    assert info.disclaimer == "Trained and evaluated on synthetic data. Simulation results only; not real-world accuracy."


def test_model_info_401_without_token(api):
    assert_envelope(api.get("/a/v1/model"), 401, "UNAUTHORIZED")


def test_model_info_503_without_artefact(api_no_model, auth_headers):
    err = assert_envelope(api_no_model.get("/a/v1/model", headers=auth_headers), 503, "MODEL_UNAVAILABLE")
    assert err["details"]["reason"] == "artefact_missing"


def test_health_still_ok_without_artefact(api_no_model):
    r = api_no_model.get("/a/v1/health")
    assert r.status_code == 200 and r.json()["model_version"] == "xgb-sa-v1"


# --------------------------------------------------------------------------- 6.5 GET /scenarios/{id}/predictions
def test_predictions_200_strict(api, auth_headers):
    r = api.get("/a/v1/scenarios/scn_sa01_w06/predictions", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == PREDICTION_KEYS
    ps = PredictionSet.model_validate(body)
    assert ps.scenario_id == "scn_sa01_w06"
    assert ps.run_mae_litres is not None and ps.run_mae_litres >= 0
    assert [a["area_id"] for a in body["areas"]] == ["ar_01", "ar_02", "ar_03"]
    for area in body["areas"]:
        assert set(area) == AREA_KEYS
        assert area["predicted_shortfall_litres"] == max(0, area["predicted_need_litres"] - area["expected_piped_litres"])
        assert len(area["top_features"]) == 3
        assert all(set(f) == {"feature", "contribution_litres"} for f in area["top_features"])
        mags = [abs(f["contribution_litres"]) for f in area["top_features"]]
        assert mags == sorted(mags, reverse=True)


def test_predictions_cached_between_requests(api, auth_headers):
    first = api.get("/a/v1/scenarios/scn_sa01_w06/predictions", headers=auth_headers).json()
    second = api.get("/a/v1/scenarios/scn_sa01_w06/predictions", headers=auth_headers).json()
    assert first == second  # same generated_at and latency => served from the cache


def test_predictions_for_derived_scenario_have_null_mae(api, auth_headers):
    r = api.get(f"/a/v1/scenarios/{DERIVED_ID}/predictions", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_mae_litres"] is None
    PredictionSet.model_validate(body)


def test_predictions_404_unknown_scenario(api, auth_headers):
    err = assert_envelope(api.get("/a/v1/scenarios/scn_sa01_w99/predictions", headers=auth_headers), 404, "SCENARIO_NOT_FOUND")
    assert err["details"] == {"scenario_id": "scn_sa01_w99"}


def test_predictions_401_without_token(api):
    assert_envelope(api.get("/a/v1/scenarios/scn_sa01_w06/predictions"), 401, "UNAUTHORIZED")


def test_predictions_503_without_artefact(api_no_model, auth_headers):
    assert_envelope(
        api_no_model.get("/a/v1/scenarios/scn_sa01_w06/predictions", headers=auth_headers), 503, "MODEL_UNAVAILABLE"
    )


def test_predictions_404_beats_503(api_no_model, auth_headers):
    assert_envelope(api_no_model.get("/a/v1/scenarios/scn_zz/predictions", headers=auth_headers), 404, "SCENARIO_NOT_FOUND")
