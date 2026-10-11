"""End-to-end model and latency checks against the real committed artefact.

* GET /model reports real numbers and XGBoost beats the last-observed baseline (nothing is tuned on the test set).
* On the three seeds the hidden-truth run MAE of the served predictions beats the baseline's too.
* Latency targets: predictions < 2 s for 10 areas (cold, fresh app), solve inside ``time_limit_s + 1.0``.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from jp_a.domain.entities import FEATURE_NAMES, ModelInfo
from jp_a.main import create_app

from .e2e_helpers import (
    MAIN_ID,
    REAL_MODELS_DIR,
    SEED_IDS,
    WIDE_ID,
    solve_body,
)

PREDICTION_BUDGET_S = 2.0


@pytest.fixture(scope="module")
def info(client_a) -> ModelInfo:
    r = client_a.http.get("/a/v1/model", headers=client_a.headers)
    assert r.status_code == 200, r.text
    return ModelInfo.model_validate(r.json())


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads((REAL_MODELS_DIR / "model_meta.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- model endpoint
def test_xgboost_beats_the_last_observed_baseline(info):
    xgb, base = info.metrics.xgboost, info.metrics.last_observed_baseline
    assert xgb.mae_litres < base.mae_litres
    assert xgb.rmse_litres < base.rmse_litres
    assert xgb.mae_litres > 0 and xgb.rmse_litres >= xgb.mae_litres  # real numbers, not placeholders


def test_model_endpoint_reports_the_committed_artefact(info, meta):
    assert info.model_version == meta["model_version"]
    assert info.train_rows == meta["train_rows"] > info.test_rows == meta["test_rows"] > 0
    assert info.metrics.model_dump() == meta["metrics"]
    assert info.target == "need_litres"


def test_model_endpoint_is_honest_about_being_a_simulation(info):
    assert info.is_simulation is True
    assert "simulation" in info.disclaimer.lower()
    assert "synthetic" in info.disclaimer.lower()


def test_feature_importances_use_the_exact_feature_names(info):
    names = [f.feature for f in info.feature_importances]
    assert len(names) == len(set(names)) and set(names) <= set(FEATURE_NAMES)
    assert all(f.importance >= 0 for f in info.feature_importances)
    importances = [f.importance for f in info.feature_importances]
    assert importances == sorted(importances, reverse=True)
    assert info.feature_importances[0].feature in {"mean_need_last3_litres", "last_need_litres"}


@pytest.mark.parametrize("scenario_id", SEED_IDS)
def test_served_predictions_beat_the_baseline_on_the_hidden_truth(client_a, data_dir, scenario_id):
    prediction = client_a.predictions(scenario_id)
    truth = json.loads((data_dir / "truth" / f"{scenario_id}.json").read_text(encoding="utf-8"))["true_need_litres"]
    model_err = [abs(p.predicted_need_litres - truth[p.area_id]) for p in prediction.areas]
    base_err = [abs(p.baseline_last_need_litres - truth[p.area_id]) for p in prediction.areas]
    model_mae = sum(model_err) / len(model_err)
    assert prediction.run_mae_litres == pytest.approx(model_mae, abs=0.1)
    assert model_mae < sum(base_err) / len(base_err)


def test_run_mae_is_the_same_on_a_repeated_request(client_a):
    a = client_a.predictions(MAIN_ID).run_mae_litres
    b = client_a.predictions(MAIN_ID).run_mae_litres
    assert a == b and a is not None


# --------------------------------------------------------------------------- latency
def test_predictions_are_fast_cold_for_every_scenario(e2e_settings, make_token):
    """A fresh app (empty prediction cache), model loaded at startup: first request per scenario < 2 s."""
    headers = {"Authorization": f"Bearer {make_token()}"}
    with TestClient(create_app(e2e_settings)) as http:
        for sid in (*SEED_IDS, WIDE_ID):
            started = time.perf_counter()
            r = http.get(f"/a/v1/scenarios/{sid}/predictions", headers=headers)
            elapsed = time.perf_counter() - started
            assert r.status_code == 200, r.text
            assert elapsed < PREDICTION_BUDGET_S, f"{sid}: {elapsed:.3f}s"
            assert r.json()["latency_ms"] < PREDICTION_BUDGET_S * 1000
        started = time.perf_counter()
        http.get(f"/a/v1/scenarios/{WIDE_ID}/predictions", headers=headers)  # cache hit
        assert time.perf_counter() - started < 0.5


def test_ten_area_prediction_latency_field_is_far_below_the_target(client_a):
    prediction = client_a.predictions(WIDE_ID)
    assert len(prediction.areas) == 10
    assert prediction.latency_ms < 500  # target < 2 s; expect well under 200 ms locally, with CI slack


@pytest.mark.parametrize("limit", [1.0, 3.0])
def test_solve_latency_stays_inside_the_requested_limit(client_a, limit):
    scenario = client_a.scenario(WIDE_ID)
    started = time.perf_counter()
    r = client_a.solve(solve_body(scenario, time_limit_s=limit))
    elapsed = time.perf_counter() - started
    assert r.status_code == 200, r.text
    assert elapsed < limit + 1.0, f"limit {limit}: {elapsed:.2f}s"
    assert r.json()["solver"]["time_limit_s"] == limit
    assert r.json()["constraint_check"]["violations"] == []


def test_solve_with_the_half_second_minimum_limit(client_a):
    scenario = client_a.scenario(WIDE_ID)
    started = time.perf_counter()
    r = client_a.solve(solve_body(scenario, time_limit_s=0.5))
    elapsed = time.perf_counter() - started
    assert r.status_code == 200
    assert elapsed < 1.5 + 0.5
    assert r.json()["solver"]["status"] in {"optimal", "feasible", "time_limited", "heuristic"}
    assert r.json()["constraint_check"]["violations"] == []
