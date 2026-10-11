"""Training CSVs: exact schema, split by service area, feature definitions, no target leakage."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jp_a.data.generator import (
    ALERT_THRESHOLDS_C,
    PIPED_ALERT_CUT,
    Weather,
    build_demo_base,
    deterministic_need,
    lag_features,
    training_service_area,
)

from .data_helpers import DATA_DIR, MAIN_ID, load_seed

HEADER = (
    "service_area_id,area_id,window_index,max_temp_c,heat_index_c,alert_level,heat_exposure,population,"
    "last_need_litres,mean_need_last3_litres,last_received_litres,expected_piped_litres,need_litres"
)
INT_COLUMNS = (
    "window_index", "alert_level", "population", "last_need_litres", "last_received_litres",
    "expected_piped_litres", "need_litres",
)  # fmt: skip
TRAIN_SAS = {f"sa_{k:02d}" for k in range(2, 34)}
TEST_SAS = {f"sa_{k:02d}" for k in range(34, 42)}


@pytest.fixture(scope="module")
def train() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "training" / "train.csv")


@pytest.fixture(scope="module")
def test_split() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "training" / "test.csv")


@pytest.fixture(scope="module")
def both(train, test_split) -> pd.DataFrame:
    return pd.concat([train, test_split], ignore_index=True)


def test_exact_header():
    for name in ("train.csv", "test.csv"):
        first_line = (DATA_DIR / "training" / name).read_text(encoding="utf-8").split("\n", 1)[0]
        assert first_line == HEADER


def test_split_by_service_area(train, test_split):
    assert set(train.service_area_id) == TRAIN_SAS
    assert set(test_split.service_area_id) == TEST_SAS
    assert "sa_01" not in set(train.service_area_id) | set(test_split.service_area_id)


def test_rows_cover_every_area_for_windows_2_to_30(both):
    for _, group in both.groupby("service_area_id"):
        n_areas = group.area_id.nunique()
        assert 6 <= n_areas <= 10
        assert len(group) == n_areas * 29
        assert set(group.area_id) == {f"ar_{k:02d}" for k in range(1, n_areas + 1)}
        for _, rows in group.groupby("area_id"):
            assert sorted(rows.window_index) == list(range(2, 31))


def test_row_counts(train, test_split):
    assert len(train) == 7482
    assert len(test_split) == 1885


def test_types_and_no_missing_values(both):
    assert not both.isna().any().any()
    for column in INT_COLUMNS:
        assert pd.api.types.is_integer_dtype(both[column]), column
    assert (both[list(INT_COLUMNS[2:])] > 0).all().all()


def test_weather_columns_are_consistent(both):
    expected_alert = sum((both.max_temp_c >= t).astype(int) for t in ALERT_THRESHOLDS_C)
    assert (both.alert_level == expected_alert).all()
    offset = both.heat_index_c - both.max_temp_c
    assert offset.between(1.95, 6.05).all()
    assert set(both.alert_level) == {0, 1, 2, 3}
    # each window has one weather value per service area
    assert (both.groupby(["service_area_id", "window_index"]).max_temp_c.nunique() == 1).all()


def test_every_alert_level_appears_in_both_splits(train, test_split):
    assert set(train.alert_level) == {0, 1, 2, 3}
    assert set(test_split.alert_level) == {0, 1, 2, 3}


def test_static_columns_are_constant_per_area(both):
    per_area = both.groupby(["service_area_id", "area_id"])
    assert (per_area.population.nunique() == 1).all()
    assert (per_area.heat_exposure.nunique() == 1).all()


def test_lag_columns_follow_their_definitions(both):
    for _, rows in both.groupby(["service_area_id", "area_id"]):
        rows = rows.sort_values("window_index")
        needs = [int(rows.last_need_litres.iloc[0]), *map(int, rows.need_litres)]  # windows 1..30
        for t, last_need, mean3, last_received in zip(
            rows.window_index, rows.last_need_litres, rows.mean_need_last3_litres, rows.last_received_litres, strict=True
        ):
            assert last_need == needs[t - 2]
            window = needs[max(1, t - 3) - 1 : t - 1]
            assert mean3 == pytest.approx(round(sum(window) / len(window), 1), abs=1e-9)
            assert 0 < last_received <= last_need


def test_features_match_the_scenario_history_definitions():
    """CSV row t == features computed the prediction-time way from a history of windows 1..t-1."""
    sa, weather, panels = training_service_area(42, 2)
    csv = pd.read_csv(DATA_DIR / "training" / "train.csv")
    csv = csv[csv.service_area_id == "sa_02"].set_index(["area_id", "window_index"])
    for a in sa.areas:
        p = panels[a.number]
        history = [{"need_litres": p.need[w], "received_litres": p.received[w]} for w in range(len(weather))]
        for t in (2, 3, 4, 6, 17, 30):
            past = history[: t - 1]
            last3 = [h["need_litres"] for h in past[-3:]]
            row = csv.loc[(a.area_id, t)]
            assert row.last_need_litres == past[-1]["need_litres"]
            assert row.mean_need_last3_litres == pytest.approx(round(sum(last3) / len(last3), 1), abs=1e-9)
            assert row.last_received_litres == past[-1]["received_litres"]
            assert row.expected_piped_litres == p.expected_piped[t - 1]
            assert row.need_litres == p.need[t - 1]


def test_scenario_history_comes_from_the_same_simulation():
    base = build_demo_base(42)
    scenario = load_seed(MAIN_ID)
    for a in scenario.areas:
        panel = base.panels[int(a.area_id[3:])]
        assert [h.need_litres for h in a.history] == panel.need
        assert [h.received_litres for h in a.history] == panel.received
        needs, received = [h.need_litres for h in a.history], [h.received_litres for h in a.history]
        assert lag_features(needs, received) == (
            a.history[-1].need_litres,
            round(sum(needs[-3:]) / 3, 1),
            a.history[-1].received_litres,
        )


def _residuals(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    det = np.array([
        deterministic_need(int(r.population), float(r.heat_exposure), Weather(float(r.max_temp_c), float(r.heat_index_c), int(r.alert_level)))
        for r in df.itertuples()
    ])  # fmt: skip
    return det, np.log(df.need_litres.to_numpy() / det)


def test_no_target_leakage_through_expected_piped(both):
    det, residual = _residuals(both)
    # the target carries real noise ...
    assert 0.06 <= residual.std() <= 0.11
    # ... which expected_piped_litres does not reveal
    assert abs(np.corrcoef(both.expected_piped_litres, residual)[0, 1]) < 0.05
    ratio = both.expected_piped_litres.to_numpy() / det
    assert abs(np.corrcoef(ratio, residual)[0, 1]) < 0.05


def test_expected_piped_is_a_fixed_share_of_noise_free_need(both):
    det, _ = _residuals(both)
    share = both.expected_piped_litres / (det * (1 - PIPED_ALERT_CUT * both.alert_level))
    spread = share.groupby([both.service_area_id, both.area_id]).agg(lambda s: s.max() - s.min())
    assert (spread < 2e-3).all()  # only integer rounding varies
    assert share.between(0.549, 0.851).all()


def test_target_is_predictable_but_not_trivial(test_split):
    baseline_mae = (test_split.need_litres - test_split.last_need_litres).abs().mean()
    assert baseline_mae > 0.05 * test_split.need_litres.mean()
