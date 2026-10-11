"""M3: feature definitions for scenarios (serving) and training CSVs."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from jp_a.domain.entities import FEATURE_NAMES, Scenario
from jp_a.ml.features import (
    CSV_COLUMNS,
    FEATURE_COLUMNS,
    FeatureError,
    build_feature_frame,
    load_training_frame,
    to_model_matrix,
)

from .ml_helpers import TRAIN_SERVICE_AREAS, load_scenario_fixture, write_training_csv


@pytest.fixture()
def scenario() -> Scenario:
    return Scenario.model_validate(load_scenario_fixture())


def test_feature_columns_match_contract_order():
    assert FEATURE_COLUMNS == list(FEATURE_NAMES)
    assert CSV_COLUMNS == [
        "service_area_id", "area_id", "window_index", "max_temp_c", "heat_index_c", "alert_level",
        "heat_exposure", "population", "last_need_litres", "mean_need_last3_litres", "last_received_litres",
        "expected_piped_litres", "need_litres",
    ]  # fmt: skip


def test_feature_frame_from_contract_example(scenario):
    frame = build_feature_frame(scenario)
    assert list(frame.columns) == list(FEATURE_NAMES)
    assert list(frame.index) == ["ar_01", "ar_02", "ar_03"]  # scenario order
    expected = {
        "ar_01": [44.5, 49.0, 2, 0.55, 18000, 53000, 52500.0, 53000, 40000],
        "ar_02": [44.5, 49.0, 2, 0.70, 11000, 32000, 31500.0, 27500, 24000],
        "ar_03": [44.5, 49.0, 2, 0.90, 7000, 20500, 20250.0, 12700, 15000],
    }
    for area_id, values in expected.items():
        assert frame.loc[area_id].tolist() == pytest.approx(values)


def test_feature_frame_dtypes(scenario):
    frame = build_feature_frame(scenario)
    for col in ("alert_level", "population", "last_need_litres", "last_received_litres", "expected_piped_litres"):
        assert frame[col].dtype == np.int64, col
    for col in ("max_temp_c", "heat_index_c", "heat_exposure", "mean_need_last3_litres"):
        assert frame[col].dtype == np.float64, col


def test_mean_need_uses_last_three_windows_only():
    data = load_scenario_fixture()
    data["areas"][0]["history"] = [
        {"window_index": w, "need_litres": n, "received_litres": n, "max_temp_c": 40.0, "alert_level": 1}
        for w, n in [(1, 10_000), (2, 20_000), (3, 30_000), (4, 40_000), (5, 50_000)]
    ]
    frame = build_feature_frame(Scenario.model_validate(data))
    assert frame.loc["ar_01", "mean_need_last3_litres"] == pytest.approx(40_000.0)
    assert frame.loc["ar_01", "last_need_litres"] == 50_000


def test_mean_need_is_rounded_to_one_decimal_like_the_training_csv():
    data = load_scenario_fixture()
    data["areas"][0]["history"] = [
        {"window_index": w, "need_litres": n, "received_litres": n, "max_temp_c": 40.0, "alert_level": 1}
        for w, n in [(3, 10_000), (4, 10_001), (5, 10_001)]
    ]
    frame = build_feature_frame(Scenario.model_validate(data))
    assert frame.loc["ar_01", "mean_need_last3_litres"] == 10_000.7


def test_single_history_window_is_enough():
    data = load_scenario_fixture()
    data["areas"][1]["history"] = data["areas"][1]["history"][-1:]
    frame = build_feature_frame(Scenario.model_validate(data))
    assert frame.loc["ar_02", "mean_need_last3_litres"] == pytest.approx(32_000.0)
    assert frame.loc["ar_02", "last_received_litres"] == 27_500


def test_area_without_history_is_rejected():
    data = load_scenario_fixture()
    data["areas"][2]["history"] = []
    with pytest.raises(FeatureError, match="ar_03"):
        build_feature_frame(Scenario.model_validate(data))


def test_heat_features_follow_the_scenario_not_the_history():
    data = copy.deepcopy(load_scenario_fixture())
    data["heat"] = {"alert_level": 3, "max_temp_c": 46.0, "heat_index_c": 51.5}
    frame = build_feature_frame(Scenario.model_validate(data))
    assert set(frame["alert_level"]) == {3}
    assert set(frame["max_temp_c"]) == {46.0}
    assert set(frame["heat_index_c"]) == {51.5}


def test_model_matrix_reorders_and_validates(scenario):
    frame = build_feature_frame(scenario)
    shuffled = frame[list(reversed(FEATURE_COLUMNS))]
    np.testing.assert_array_equal(to_model_matrix(shuffled), to_model_matrix(frame))
    assert to_model_matrix(frame).dtype == np.float64
    with pytest.raises(FeatureError, match="missing"):
        to_model_matrix(frame.drop(columns=["population"]))
    broken = frame.astype(float)
    broken.iloc[0, 0] = np.nan
    with pytest.raises(FeatureError, match="NaN"):
        to_model_matrix(broken)


# --------------------------------------------------------------------------- training CSV loading
@pytest.fixture()
def csv_path(tmp_path):
    return write_training_csv(tmp_path / "train.csv", TRAIN_SERVICE_AREAS[:2], seed=3, n_areas=3, n_windows=6)


def test_load_training_frame_ok(csv_path):
    frame = load_training_frame(csv_path)
    assert list(frame.columns) == CSV_COLUMNS
    assert len(frame) == 2 * 3 * 5  # windows 2..6
    assert frame["population"].dtype == np.int64
    assert frame["need_litres"].dtype == np.int64
    assert frame["mean_need_last3_litres"].dtype == np.float64
    assert frame["window_index"].min() == 2


def test_load_training_frame_accepts_integral_floats(csv_path, tmp_path):
    frame = pd.read_csv(csv_path)
    frame["population"] = frame["population"].astype(float)
    path = tmp_path / "floats.csv"
    frame.to_csv(path, index=False)
    assert load_training_frame(path)["population"].dtype == np.int64


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda f: f.rename(columns={"need_litres": "need"}), "header"),
        (lambda f: f[[c for c in CSV_COLUMNS if c != "population"] + ["population"]], "header"),
        (lambda f: f.assign(extra=1), "header"),
        (lambda f: f.assign(last_need_litres=f["last_need_litres"].astype(float).where(f.index != 0)), "missing values"),
        (lambda f: f.assign(need_litres=f["need_litres"] + 0.5), "integer"),
        (lambda f: f.assign(window_index=1), "window_index"),
        (lambda f: f.assign(service_area_id="area-1"), "service_area_id"),
        (lambda f: f.iloc[0:0], "no data rows"),
    ],
)
def test_load_training_frame_rejects_bad_files(csv_path, tmp_path, mutate, match):
    path = tmp_path / "bad.csv"
    mutate(pd.read_csv(csv_path, dtype={"service_area_id": str, "area_id": str})).to_csv(path, index=False)
    with pytest.raises(FeatureError, match=match):
        load_training_frame(path)


def test_load_training_frame_missing_file(tmp_path):
    with pytest.raises(FeatureError, match="not found"):
        load_training_frame(tmp_path / "nope.csv")
