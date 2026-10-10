"""Structure of the three committed seed scenarios (CONTRACT 5.3, MEMBER_A F1)."""
from __future__ import annotations

import json
import re

import pytest

from jp_a.data.generator import ALERT_THRESHOLDS_C, alert_level_for
from jp_a.data.geo import ROAD_FACTOR, haversine_km
from jp_a.domain.entities import Scenario, parse_iso

from .data_helpers import DATA_DIR, DEPOT, MAIN_ID, MILD_ID, SEED_IDS, SEVERE_ID, load_seed, load_truth, true_shortfalls

CONTRACT_SCENARIO_KEYS = {
    "schema_version", "scenario_id", "parent_scenario_id", "service_area_id", "service_area_name", "window_index",
    "window_start", "window_hours", "is_synthetic", "seed", "heat", "supply", "depot", "tankers", "areas",
    "distance_km", "disruptions_applied",
}  # fmt: skip
CONTRACT_AREA_KEYS = {
    "area_id", "name", "lat", "lon", "population", "vulnerability_index", "vulnerability_available",
    "heat_exposure", "expected_piped_litres", "requested_litres", "request_time", "last_delivery_litres",
    "last_delivery_at", "history",
}  # fmt: skip


@pytest.fixture(scope="module", params=SEED_IDS)
def seed(request) -> Scenario:
    return load_seed(request.param)


def test_seed_file_has_exactly_the_contract_keys():
    for sid in SEED_IDS:
        raw = json.loads((DATA_DIR / "scenarios" / f"{sid}.json").read_text(encoding="utf-8"))
        assert set(raw) == CONTRACT_SCENARIO_KEYS
        assert all(set(a) == CONTRACT_AREA_KEYS for a in raw["areas"])
        assert "true_need_litres" not in json.dumps(raw)


def test_seed_header(seed):
    assert seed.schema_version == "1.0"
    assert seed.parent_scenario_id is None
    assert seed.service_area_id == "sa_01"
    assert seed.service_area_name == "Synthetic Service Area 01"
    assert seed.window_index == 6 and seed.window_hours == 12
    assert seed.window_start == "2026-05-21T06:00:00.000Z"
    assert seed.is_synthetic is True and seed.seed == 42
    assert seed.disruptions_applied == []
    assert seed.depot.model_dump() == {
        "depot_id": "dp_01", "name": "Central Depot (synthetic)", "lat": 28.6692, "lon": 77.4538,
    }  # fmt: skip


def test_tankers(seed):
    assert [t.tanker_id for t in seed.tankers] == ["tk_01", "tk_02", "tk_03", "tk_04"]
    for t in seed.tankers:
        assert 10_000 <= t.capacity_litres <= 12_000 and t.capacity_litres % 100 == 0
        assert t.status == "available" and t.max_route_km == 60.0
    # tanker water is the binding resource: it fits in the fleet's single-trip capacity
    assert seed.supply.tanker_water_available_litres <= sum(t.capacity_litres for t in seed.tankers)


def test_areas(seed):
    assert [a.area_id for a in seed.areas] == [f"ar_{k:02d}" for k in range(1, 9)]
    for a in seed.areas:
        assert re.fullmatch(r"Ward \d{2} \(synthetic\)", a.name)
        assert a.vulnerability_available is True
        assert 0 <= a.vulnerability_index <= 1 and 0 <= a.heat_exposure <= 1
        assert a.population > 0 and a.expected_piped_litres > 0
        assert haversine_km(seed.depot.lat, seed.depot.lon, a.lat, a.lon) <= 6.0


def test_history(seed):
    for a in seed.areas:
        assert [h.window_index for h in a.history] == [1, 2, 3, 4, 5]
        for h in a.history:
            assert 0 < h.received_litres <= h.need_litres
            assert h.alert_level == alert_level_for(h.max_temp_c)


def test_heat_block_is_consistent(seed):
    assert seed.heat.alert_level == alert_level_for(seed.heat.max_temp_c)
    assert 2.0 <= seed.heat.heat_index_c - seed.heat.max_temp_c <= 6.0


def test_alert_thresholds():
    assert ALERT_THRESHOLDS_C == (38.0, 44.0, 47.0)
    assert [alert_level_for(t) for t in (37.9, 38.0, 43.9, 44.0, 46.9, 47.0, 50.0)] == [0, 1, 1, 2, 2, 3, 3]


def test_distance_matrix(seed):
    nodes = {DEPOT: (seed.depot.lat, seed.depot.lon)} | {a.area_id: (a.lat, a.lon) for a in seed.areas}
    assert list(seed.distance_km) == list(nodes)
    for i, (lat_i, lon_i) in nodes.items():
        assert list(seed.distance_km[i]) == list(nodes)
        assert seed.distance_km[i][i] == 0.0
        for j, (lat_j, lon_j) in nodes.items():
            d = seed.distance_km[i][j]
            assert d == seed.distance_km[j][i]
            assert d == round(d, 2)
            if i != j:
                assert d == pytest.approx(ROAD_FACTOR * haversine_km(lat_i, lon_i, lat_j, lon_j), abs=0.006)
                assert d > 0


def test_request_and_delivery_times(seed):
    start = parse_iso(seed.window_start)
    for a in seed.areas:
        lead_h = (start - parse_iso(a.request_time)).total_seconds() / 3600
        assert -2.0 <= lead_h <= 4.0
        if a.last_delivery_at is None:
            assert a.last_delivery_litres == 0
        else:
            assert a.last_delivery_litres > 0 and a.last_delivery_litres % 100 == 0
            assert parse_iso(a.last_delivery_at) < start
    assert len({a.request_time for a in seed.areas}) == len(seed.areas), "request times must be distinct"
    km = [seed.distance_km[DEPOT][a.area_id] for a in seed.areas]
    assert len(set(km)) == len(km), "depot distances must be distinct"


def test_requested_litres_are_round_hundreds(seed):
    assert all(a.requested_litres % 100 == 0 for a in seed.areas)


def test_truth_files_match_scenarios():
    for sid in SEED_IDS:
        truth = load_truth(sid)
        scenario = load_seed(sid)
        assert set(truth) == {"scenario_id", "window_index", "true_need_litres"}
        assert truth["scenario_id"] == sid and truth["window_index"] == 6
        assert list(truth["true_need_litres"]) == [a.area_id for a in scenario.areas]
        assert all(isinstance(v, int) and v > 0 for v in truth["true_need_litres"].values())


@pytest.mark.parametrize(
    ("scenario_id", "alert", "max_temp", "heat_index", "share"),
    [(MAIN_ID, 2, 44.5, 49.0, 0.55), (SEVERE_ID, 3, 47.5, 52.5, 0.35), (MILD_ID, 1, 41.5, 45.0, 0.85)],
)
def test_heat_and_supply_ratio(scenario_id, alert, max_temp, heat_index, share):
    scenario = load_seed(scenario_id)
    assert (scenario.heat.alert_level, scenario.heat.max_temp_c, scenario.heat.heat_index_c) == (alert, max_temp, heat_index)
    total = sum(true_shortfalls(scenario, load_truth(scenario_id)).values())
    supply = scenario.supply.tanker_water_available_litres
    assert supply % 100 == 0
    assert supply / total == pytest.approx(share, abs=0.005)


def test_seeds_share_geometry_history_and_fleet():
    main, severe, mild = (load_seed(sid) for sid in SEED_IDS)
    for other in (severe, mild):
        assert other.distance_km == main.distance_km
        assert other.tankers == main.tankers
        for a, b in zip(main.areas, other.areas, strict=True):
            static = ("area_id", "name", "lat", "lon", "population", "vulnerability_index", "heat_exposure",
                      "history", "last_delivery_litres", "last_delivery_at", "request_time")  # fmt: skip
            assert {k: getattr(a, k) for k in static} == {k: getattr(b, k) for k in static}


def test_heat_drives_demand_across_seeds():
    shortfall = {sid: sum(true_shortfalls(load_seed(sid), load_truth(sid)).values()) for sid in SEED_IDS}
    assert shortfall[MILD_ID] < shortfall[MAIN_ID] < shortfall[SEVERE_ID]


def test_label_table():
    labels = json.loads((DATA_DIR / "labels.json").read_text(encoding="utf-8"))
    assert labels == {
        MAIN_ID: "Main demo · heat alert 2 · supply ≈55% of shortfall",
        SEVERE_ID: "Severe heat · heat alert 3 · supply ≈35% of shortfall",
        MILD_ID: "Mild heat · heat alert 1 · supply ≈85% of shortfall",
    }
