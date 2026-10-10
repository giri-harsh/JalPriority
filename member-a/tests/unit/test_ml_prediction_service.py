"""M4: PredictionService - F-1, SHAP top features, run_mae, cache, ordering, latency, error paths."""
from __future__ import annotations

import re
import threading

import numpy as np
import pytest

from jp_a.api.errors import ContractValidationError, ModelUnavailableError, ScenarioNotFoundError
from jp_a.domain.entities import FEATURE_NAMES, ISO_DT_PATTERN, PredictionSet, Scenario
from jp_a.ml.features import build_feature_frame
from jp_a.repositories.model_repo import ModelRepository
from jp_a.services.prediction_service import PredictionService, run_mae, shortfall_litres, top_contributions

from .ml_helpers import (
    DERIVED_ID,
    TRUE_NEED,
    FakeScenarioRepo,
    load_scenario_fixture,
    ten_area_scenario_dict,
    train_synthetic_model,
)


@pytest.fixture(scope="module")
def trained_dir(tmp_path_factory):
    models_dir, _ = train_synthetic_model(tmp_path_factory.mktemp("ml_pred"))
    return models_dir


@pytest.fixture()
def model_repo(settings, trained_dir):
    return ModelRepository(settings.model_copy(update={"a_models_dir": trained_dir}))


@pytest.fixture()
def repo():
    return FakeScenarioRepo()


@pytest.fixture()
def service(settings, repo, model_repo):
    return PredictionService(settings, repo, model_repo)


# --------------------------------------------------------------------------- pure helpers
@pytest.mark.parametrize("need,piped,expected", [(54000, 40000, 14000), (30000, 40000, 0), (40000, 40000, 0), (0, 0, 0)])
def test_shortfall_is_f1(need, piped, expected):
    assert shortfall_litres(need, piped) == expected


def test_top_contributions_sorted_by_magnitude_and_excludes_bias():
    row = [10.0, -500.04, 0.0, 300.0, -300.0, 1.0, 2.0, 3.0, 4.0, 99999.0]  # last = bias
    top = top_contributions(row)
    assert [(c.feature, c.contribution_litres) for c in top] == [
        ("heat_index_c", -500.0),
        ("heat_exposure", 300.0),  # tie on |300| keeps FEATURE_NAMES order
        ("population", -300.0),
    ]


def test_top_contributions_never_emits_negative_zero():
    top = top_contributions([-0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0])
    assert all(str(c.contribution_litres) != "-0.0" for c in top)


def test_run_mae_helper():
    assert run_mae({"ar_01": 100, "ar_02": 200}, {"true_need_litres": {"ar_01": 110, "ar_02": 170}}) == 20.0
    assert run_mae({"ar_01": 100}, {"true_need_litres": {"ar_01": 100.25}}) == 0.2
    assert run_mae({"ar_01": 100}, None) is None
    assert run_mae({"ar_01": 100}, {"true_need_litres": {}}) is None
    assert run_mae({"ar_01": 100, "ar_09": 5}, {"true_need_litres": {"ar_01": 90}}) == 10.0


# --------------------------------------------------------------------------- service
def test_prediction_set_is_strictly_valid(service):
    result = service.get_predictions("scn_sa01_w06")
    payload = result.model_dump(mode="json")
    assert PredictionSet.model_validate(payload) == result
    assert set(payload) == {
        "schema_version", "scenario_id", "model_version", "generated_at", "latency_ms",
        "run_mae_litres", "is_simulation", "areas",
    }  # fmt: skip
    assert payload["schema_version"] == "1.0"
    assert payload["model_version"] == "xgb-sa-v1"
    assert payload["is_simulation"] is True
    assert re.match(ISO_DT_PATTERN, payload["generated_at"])
    assert isinstance(payload["latency_ms"], int) and payload["latency_ms"] >= 0


def test_areas_follow_scenario_order_and_inputs(service, repo):
    scenario = repo.get("scn_sa01_w06")
    result = service.get_predictions("scn_sa01_w06")
    assert [a.area_id for a in result.areas] == [a.area_id for a in scenario.areas]
    for pred, area in zip(result.areas, scenario.areas, strict=True):
        assert pred.expected_piped_litres == area.expected_piped_litres
        assert pred.baseline_last_need_litres == area.history[-1].need_litres


def test_f1_holds_for_every_area(service):
    for area in service.get_predictions("scn_sa01_w06").areas:
        assert isinstance(area.predicted_shortfall_litres, int)
        assert area.predicted_shortfall_litres == max(0, area.predicted_need_litres - area.expected_piped_litres)


def test_predicted_need_matches_model_output(service, model_repo, repo):
    scenario = repo.get("scn_sa01_w06")
    preds, contribs = model_repo.predict(build_feature_frame(scenario))
    result = service.predict_for_scenario(scenario)
    assert [a.predicted_need_litres for a in result.areas] == [int(round(max(0.0, p))) for p in preds]
    for i, area in enumerate(result.areas):
        assert len(area.top_features) == 3
        names = [c.feature for c in area.top_features]
        assert len(set(names)) == 3 and set(names) <= set(FEATURE_NAMES)
        mags = [abs(c.contribution_litres) for c in area.top_features]
        assert mags == sorted(mags, reverse=True)
        # The chosen features are the true top-3 by |SHAP| among the nine features (bias excluded).
        row = contribs[i, : len(FEATURE_NAMES)]
        assert min(mags) >= round(float(np.sort(np.abs(row))[-3]), 1) - 0.05
        for c in area.top_features:
            assert c.contribution_litres == round(float(row[FEATURE_NAMES.index(c.feature)]), 1)
            assert isinstance(c.contribution_litres, float)


def test_run_mae_for_seed_against_truth(service):
    result = service.get_predictions("scn_sa01_w06")
    expected = round(sum(abs(a.predicted_need_litres - TRUE_NEED[a.area_id]) for a in result.areas) / 3, 1)
    assert result.run_mae_litres == expected


def test_run_mae_none_for_derived(service):
    result = service.get_predictions(DERIVED_ID)
    assert result.run_mae_litres is None
    assert result.scenario_id == DERIVED_ID


def test_run_mae_none_without_truth(settings, model_repo):
    service = PredictionService(settings, FakeScenarioRepo(truth={}), model_repo)
    assert service.get_predictions("scn_sa01_w06").run_mae_litres is None


def test_cache_returns_same_object(service, repo):
    first = service.get_predictions("scn_sa01_w06")
    calls = repo.get_calls
    second = service.get_predictions("scn_sa01_w06")
    assert second is first
    assert repo.get_calls == calls  # served from the cache without touching the repository
    assert service.predict_for_scenario(repo.get("scn_sa01_w06")) is first


def test_cache_is_per_scenario(service):
    seed = service.get_predictions("scn_sa01_w06")
    derived = service.get_predictions(DERIVED_ID)
    assert seed is not derived
    assert service.get_predictions(DERIVED_ID) is derived


def test_clear_cache_recomputes_equal_values(service):
    first = service.get_predictions("scn_sa01_w06")
    service.clear_cache()
    second = service.get_predictions("scn_sa01_w06")
    assert second is not first
    assert second.areas == first.areas
    assert second.run_mae_litres == first.run_mae_litres


def test_concurrent_requests_share_one_result(service):
    results: list[PredictionSet] = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        results.append(service.get_predictions("scn_sa01_w06"))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 6 and all(r is results[0] for r in results)


def test_unknown_scenario_raises_404(service):
    with pytest.raises(ScenarioNotFoundError):
        service.get_predictions("scn_sa01_w99")


def test_model_unavailable_raises_503(settings, repo, tmp_path):
    service = PredictionService(settings, repo, ModelRepository(settings.model_copy(update={"a_models_dir": tmp_path})))
    with pytest.raises(ModelUnavailableError):
        service.get_predictions("scn_sa01_w06")
    with pytest.raises(ScenarioNotFoundError):  # 404 still wins for unknown ids
        service.get_predictions("scn_nope")


def test_area_without_history_is_validation_error(settings, model_repo):
    data = load_scenario_fixture()
    data["areas"][0]["history"] = []
    service = PredictionService(settings, FakeScenarioRepo(scenarios=[data]), model_repo)
    with pytest.raises(ContractValidationError) as exc_info:
        service.get_predictions("scn_sa01_w06")
    assert exc_info.value.status_code == 422


def test_ten_area_latency_under_two_seconds(settings, model_repo):
    data = ten_area_scenario_dict()
    service = PredictionService(settings, FakeScenarioRepo(scenarios=[data], truth={}), model_repo)
    model_repo.load()
    result = service.predict_for_scenario(Scenario.model_validate(data))
    assert len(result.areas) == 10
    assert result.latency_ms < 2000
