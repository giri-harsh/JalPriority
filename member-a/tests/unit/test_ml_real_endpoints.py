"""M4 end to end: the real ScenarioRepository + the committed model behind the real app (create_app + TestClient).

The app is built exactly as in production (``create_app(Settings(...))`` -> ``build_container``); only
``A_DATA_DIR`` points at a temporary copy of ``data/`` so derived scenarios never land in the repository.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from jp_a.domain.entities import FEATURE_NAMES, ModelInfo, PredictionSet, Scenario
from jp_a.main import create_app

from .ml_helpers import REAL_MODELS_DIR, SEED_SCENARIO_IDS, copy_real_data, widen_scenario

LATENCY_BUDGET_S = 2.0
WIDE_ID = "scn_sa01_w07"  # a 10-area seed-shaped scenario written into the temporary data dir
WIDE_AREAS = 10


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    path = copy_real_data(tmp_path_factory.mktemp("ml_real_data"))
    seed = json.loads((path / "scenarios" / "scn_sa01_w06.json").read_text(encoding="utf-8"))
    wide = Scenario.model_validate(widen_scenario(seed, WIDE_ID, WIDE_AREAS))
    with open(path / "scenarios" / f"{WIDE_ID}.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(wide.model_dump(mode="json"), fh, indent=2)
    return path


@pytest.fixture(scope="module")
def real_settings(settings, data_dir):
    return settings.model_copy(update={"a_data_dir": data_dir, "a_models_dir": REAL_MODELS_DIR})


@pytest.fixture(scope="module")
def api(real_settings):
    with TestClient(create_app(real_settings)) as c:
        yield c


@pytest.fixture(scope="module")
def headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture(scope="module")
def meta():
    return json.loads((REAL_MODELS_DIR / "model_meta.json").read_text(encoding="utf-8"))


def _scenario(api, headers, scenario_id: str) -> Scenario:
    r = api.get(f"/a/v1/scenarios/{scenario_id}", headers=headers)
    assert r.status_code == 200, r.text
    return Scenario.model_validate(r.json())


def _timed_predictions(api, headers, scenario_id: str) -> tuple[PredictionSet, float]:
    started = time.perf_counter()
    r = api.get(f"/a/v1/scenarios/{scenario_id}/predictions", headers=headers)
    elapsed = time.perf_counter() - started
    assert r.status_code == 200, r.text
    return PredictionSet.model_validate(r.json()), elapsed


def _truth(data_dir, scenario_id: str) -> dict:
    return json.loads((data_dir / "truth" / f"{scenario_id}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- GET /model
def test_model_info_serves_the_committed_numbers(api, headers, meta):
    r = api.get("/a/v1/model", headers=headers)
    assert r.status_code == 200, r.text
    info = ModelInfo.model_validate(r.json())
    assert info.metrics.model_dump() == meta["metrics"]
    assert info.train_rows == meta["train_rows"] and info.test_rows == meta["test_rows"]
    assert info.trained_at == meta["trained_at"]
    assert [f.model_dump() for f in info.feature_importances] == meta["feature_importances"]
    assert info.metrics.xgboost.mae_litres < info.metrics.last_observed_baseline.mae_litres


# --------------------------------------------------------------------------- seeds
@pytest.mark.parametrize("scenario_id", SEED_SCENARIO_IDS)
def test_seed_predictions_are_contract_exact(api, headers, data_dir, scenario_id):
    scenario = _scenario(api, headers, scenario_id)
    predictions, elapsed = _timed_predictions(api, headers, scenario_id)

    assert predictions.scenario_id == scenario_id
    assert predictions.model_version == "xgb-sa-v1"
    assert predictions.is_simulation is True
    assert elapsed < LATENCY_BUDGET_S
    assert 0 <= predictions.latency_ms < LATENCY_BUDGET_S * 1000
    assert [a.area_id for a in predictions.areas] == [a.area_id for a in scenario.areas]

    for area, pred in zip(scenario.areas, predictions.areas, strict=True):
        assert pred.expected_piped_litres == area.expected_piped_litres
        assert pred.predicted_shortfall_litres == max(0, pred.predicted_need_litres - pred.expected_piped_litres)  # F-1
        assert pred.baseline_last_need_litres == area.history[-1].need_litres
        assert pred.predicted_need_litres > 0
        assert len(pred.top_features) == 3
        names = [f.feature for f in pred.top_features]
        assert len(set(names)) == 3 and set(names) <= set(FEATURE_NAMES)
        magnitudes = [abs(f.contribution_litres) for f in pred.top_features]
        assert magnitudes == sorted(magnitudes, reverse=True)

    truth = _truth(data_dir, scenario_id)["true_need_litres"]
    assert isinstance(predictions.run_mae_litres, float)
    expected_mae = sum(abs(p.predicted_need_litres - truth[p.area_id]) for p in predictions.areas) / len(predictions.areas)
    assert predictions.run_mae_litres == round(expected_mae, 1)


def test_seed_predictions_are_cached(api, headers):
    first, _ = _timed_predictions(api, headers, "scn_sa01_w06")
    second, _ = _timed_predictions(api, headers, "scn_sa01_w06")
    assert first == second


def test_ten_area_scenario_within_latency_budget(api, headers):
    predictions, elapsed = _timed_predictions(api, headers, WIDE_ID)
    assert len(predictions.areas) == WIDE_AREAS
    assert elapsed < LATENCY_BUDGET_S
    assert predictions.latency_ms < LATENCY_BUDGET_S * 1000
    assert predictions.run_mae_litres is None  # no hidden truth for this scenario
    for pred in predictions.areas:
        assert pred.predicted_shortfall_litres == max(0, pred.predicted_need_litres - pred.expected_piped_litres)


# --------------------------------------------------------------------------- derived scenario (heat alert)
def test_heat_alert_derived_scenario_raises_predicted_need(api, headers, data_dir):
    parent = _scenario(api, headers, "scn_sa01_w06")
    assert parent.heat.alert_level < 3
    r = api.post(
        "/a/v1/scenarios/scn_sa01_w06/disruptions",
        headers=headers,
        json={"type": "heat_alert", "params": {"alert_level": 3}},
    )
    assert r.status_code == 201, r.text
    child = Scenario.model_validate(r.json())
    assert child.parent_scenario_id == "scn_sa01_w06"
    assert child.heat.max_temp_c > parent.heat.max_temp_c
    assert (data_dir / "derived" / f"{child.scenario_id}.json").is_file()

    parent_pred, _ = _timed_predictions(api, headers, parent.scenario_id)
    child_pred, elapsed = _timed_predictions(api, headers, child.scenario_id)
    assert child_pred.scenario_id == child.scenario_id
    assert child_pred.run_mae_litres is None
    assert elapsed < LATENCY_BUDGET_S

    parent_need = {a.area_id: a.predicted_need_litres for a in parent_pred.areas}
    for pred in child_pred.areas:
        assert pred.predicted_need_litres >= parent_need[pred.area_id], pred.area_id
        assert pred.predicted_shortfall_litres == max(0, pred.predicted_need_litres - pred.expected_piped_litres)
    assert sum(a.predicted_need_litres for a in child_pred.areas) > sum(parent_need.values())
