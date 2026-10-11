"""MEMBER_A section 6 item 5: disruptions (CONTRACT 5.12) through the REAL app.

Each type's effect, the derived id (regex + an independently recomputed sha1 hash), 201 then 200 idempotency,
GET of derived scenarios (also after an app restart on the same data dir), predictions of a derived scenario
(``run_mae_litres`` null) and solving derived scenarios (tanker_down: no deliveries by that tanker; supply_cut:
``totals.supply_available_litres == floor(S*(1-f)/100)*100``).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from jp_a.domain.entities import PredictionSet, Scenario
from jp_a.main import create_app

from .contract_helpers import (
    API,
    DERIVED_SCENARIO_RE,
    ISO_RE,
    SEEDS,
    assert_formulas,
    assert_iso_datetimes,
    assert_keys,
    assert_prediction_keys,
    assert_scenario_keys,
    bearer,
    copy_real_data,
    real_settings,
)

WEIGHTS = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
DEBT = {"ar_01": 0, "ar_02": 8000, "ar_03": 12000, "ar_05": 2500}


# --------------------------------------------------------------------------- independent re-implementation of 5.12
def expected_id(root: str, applied: list[dict]) -> str:
    """``{root}__d{k}_{h}``: h = first 6 hex of sha1(json.dumps([{type, params}...], sort_keys, compact separators))."""
    payload = json.dumps([{"type": a["type"], "params": a["params"]} for a in applied], sort_keys=True, separators=(",", ":"))
    return f"{root}__d{len(applied)}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:6]}"


def supply_after_cut(supply: int, fraction: float) -> int:
    """S' = floor(S * (1 - f) / 100) * 100 in exact decimal arithmetic (see CONTRACT_ISSUES CI-3)."""
    exact = Decimal(supply) * (1 - Decimal(repr(fraction))) / 100
    return math.floor(exact) * 100


# --------------------------------------------------------------------------- fixtures / helpers
@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return copy_real_data(tmp_path_factory.mktemp("contract_disruptions_data"))


@pytest.fixture(scope="module")
def cfg(settings, data_dir):
    return real_settings(settings, data_dir)


@pytest.fixture(scope="module")
def api(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


@pytest.fixture(scope="module")
def hdr(make_token):
    return bearer(make_token())


def get_scenario(api, hdr, sid: str) -> dict:
    r = api.get(f"{API}/scenarios/{sid}", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def disrupt(api, hdr, sid: str, dtype: str, params: dict):
    return api.post(f"{API}/scenarios/{sid}/disruptions", json={"type": dtype, "params": params}, headers=hdr)


def apply_new(api, hdr, sid: str, dtype: str, params: dict) -> dict:
    """First application of a disruption: 201 + a strict, well-formed derived scenario."""
    r = disrupt(api, hdr, sid, dtype, params)
    assert r.status_code == 201, r.text
    body = r.json()
    Scenario.model_validate(body)
    assert_scenario_keys(body)
    assert_iso_datetimes(body)
    return body


def assert_derived_basics(parent: dict, child: dict, dtype: str, params: dict) -> None:
    """Identity, lineage, applied-list and id/hash rules shared by every disruption type."""
    assert child["schema_version"] == "1.0" and child["is_synthetic"] is True
    assert child["parent_scenario_id"] == parent["scenario_id"]
    assert DERIVED_SCENARIO_RE.match(child["scenario_id"])
    applied = child["disruptions_applied"]
    assert len(applied) == len(parent["disruptions_applied"]) + 1
    assert applied[:-1] == parent["disruptions_applied"], "the parent's list is kept verbatim (incl. applied_at)"
    last = applied[-1]
    assert_keys(last, {"type", "params", "applied_at"}, "disruption entry")
    assert last["type"] == dtype and last["params"] == params
    assert ISO_RE.match(last["applied_at"])
    root = parent["scenario_id"].split("__", 1)[0]
    assert child["scenario_id"] == expected_id(root, applied)
    assert child["scenario_id"].startswith(f"{root}__d{len(applied)}_")
    # everything the disruption does not touch is carried over unchanged
    for key in ("service_area_id", "service_area_name", "window_index", "window_start", "window_hours", "seed", "depot", "areas", "distance_km"):
        assert child[key] == parent[key], key


def assert_idempotent(api, hdr, parent_id: str, dtype: str, params: dict, first: dict) -> None:
    again = disrupt(api, hdr, parent_id, dtype, params)
    assert again.status_code == 200, again.text
    assert again.json() == first  # same scenario, including the original applied_at
    assert get_scenario(api, hdr, first["scenario_id"]) == first


# --------------------------------------------------------------------------- tanker_down
@pytest.mark.parametrize("sid,tanker_id", [("scn_sa01_w06", f"tk_0{n}") for n in range(1, 5)] + [("scn_sa01_w06_severe", "tk_02"), ("scn_sa01_w06_mild", "tk_04")])
def test_tanker_down_effect_id_and_idempotency(api, hdr, sid, tanker_id):
    parent = get_scenario(api, hdr, sid)
    params = {"tanker_id": tanker_id}
    child = apply_new(api, hdr, sid, "tanker_down", params)
    assert_derived_basics(parent, child, "tanker_down", params)

    assert child["heat"] == parent["heat"] and child["supply"] == parent["supply"]
    assert [t["tanker_id"] for t in child["tankers"]] == [t["tanker_id"] for t in parent["tankers"]]
    for before, after in zip(parent["tankers"], child["tankers"], strict=True):
        if before["tanker_id"] == tanker_id:
            assert after == {**before, "status": "unavailable"}
        else:
            assert after == before
    assert_idempotent(api, hdr, sid, "tanker_down", params, child)


def test_tanker_down_hash_is_over_type_and_params_only(api, hdr):
    parent = get_scenario(api, hdr, "scn_sa01_w06")
    child = get_scenario(api, hdr, apply_new_or_get(api, hdr, "scn_sa01_w06", "tanker_down", {"tanker_id": "tk_01"})["scenario_id"])
    payload = '[{"params":{"tanker_id":"tk_01"},"type":"tanker_down"}]'  # sort_keys + compact separators
    assert child["scenario_id"] == f"scn_sa01_w06__d1_{hashlib.sha1(payload.encode()).hexdigest()[:6]}"
    assert child["parent_scenario_id"] == parent["scenario_id"]


def apply_new_or_get(api, hdr, sid, dtype, params) -> dict:
    r = disrupt(api, hdr, sid, dtype, params)
    assert r.status_code in (200, 201), r.text
    return r.json()


# --------------------------------------------------------------------------- supply_cut
FRACTIONS = [0.05, 0.1, 0.25, 0.3, 0.33, 0.5, 0.7, 0.9]


@pytest.mark.parametrize("fraction", FRACTIONS)
@pytest.mark.parametrize("sid", SEEDS)
def test_supply_cut_effect_id_and_idempotency(api, hdr, sid, fraction):
    parent = get_scenario(api, hdr, sid)
    params = {"fraction": fraction}
    child = apply_new(api, hdr, sid, "supply_cut", params)
    assert_derived_basics(parent, child, "supply_cut", params)

    supply = parent["supply"]["tanker_water_available_litres"]
    new_supply = child["supply"]["tanker_water_available_litres"]
    assert new_supply == supply_after_cut(supply, fraction)
    assert new_supply % 100 == 0 and 0 <= new_supply < supply
    assert child["heat"] == parent["heat"] and child["tankers"] == parent["tankers"]
    assert_idempotent(api, hdr, sid, "supply_cut", params, child)


def test_supply_cut_known_values(api, hdr):
    """Hand-computed S' for the main demo supply (24 400 L)."""
    expected = {0.05: 23100, 0.1: 21900, 0.25: 18300, 0.3: 17000, 0.5: 12200, 0.7: 7300, 0.9: 2400}
    for fraction, litres in expected.items():
        child = apply_new_or_get(api, hdr, "scn_sa01_w06", "supply_cut", {"fraction": fraction})
        assert child["supply"]["tanker_water_available_litres"] == litres, fraction


# --------------------------------------------------------------------------- heat_alert
@pytest.mark.parametrize(
    "sid,level",
    [(sid, level) for sid, own in zip(SEEDS, (2, 3, 1), strict=True) for level in range(4) if level != own],
)
def test_heat_alert_effect_id_and_idempotency(api, hdr, sid, level):
    parent = get_scenario(api, hdr, sid)
    params = {"alert_level": level}
    child = apply_new(api, hdr, sid, "heat_alert", params)
    assert_derived_basics(parent, child, "heat_alert", params)

    delta = level - parent["heat"]["alert_level"]
    assert delta != 0
    assert child["heat"] == {
        "alert_level": level,
        "max_temp_c": round(parent["heat"]["max_temp_c"] + 1.5 * delta, 1),
        "heat_index_c": round(parent["heat"]["heat_index_c"] + 2.5 * delta, 1),
    }
    assert child["supply"] == parent["supply"] and child["tankers"] == parent["tankers"]
    assert_idempotent(api, hdr, sid, "heat_alert", params, child)


# --------------------------------------------------------------------------- chains
def test_chained_disruptions_accumulate_and_keep_the_seed_as_root(api, hdr):
    seed = get_scenario(api, hdr, "scn_sa01_w06_severe")
    steps = [("tanker_down", {"tanker_id": "tk_03"}), ("supply_cut", {"fraction": 0.4}), ("heat_alert", {"alert_level": 0})]

    parent = seed
    history: list[dict] = []
    for k, (dtype, params) in enumerate(steps, start=1):
        child = apply_new(api, hdr, parent["scenario_id"], dtype, params)
        assert_derived_basics(parent, child, dtype, params)
        history.append({"type": dtype, "params": params})
        assert child["scenario_id"] == expected_id("scn_sa01_w06_severe", history)
        assert child["scenario_id"].startswith(f"scn_sa01_w06_severe__d{k}_")
        assert [{"type": d["type"], "params": d["params"]} for d in child["disruptions_applied"]] == history
        assert_idempotent(api, hdr, parent["scenario_id"], dtype, params, child)
        parent = child

    final = parent
    assert [t["status"] for t in final["tankers"] if t["tanker_id"] == "tk_03"] == ["unavailable"]
    assert final["supply"]["tanker_water_available_litres"] == supply_after_cut(seed["supply"]["tanker_water_available_litres"], 0.4)
    assert final["heat"]["alert_level"] == 0  # 3 -> 0: delta = -3
    assert final["heat"]["max_temp_c"] == round(seed["heat"]["max_temp_c"] - 4.5, 1)
    assert final["heat"]["heat_index_c"] == round(seed["heat"]["heat_index_c"] - 7.5, 1)
    assert final["parent_scenario_id"] != "scn_sa01_w06_severe"  # parent = the scenario it was applied to, not the root


def test_application_order_changes_the_id(api, hdr):
    a = apply_new_or_get(api, hdr, "scn_sa01_w06_mild", "tanker_down", {"tanker_id": "tk_01"})
    ab = apply_new_or_get(api, hdr, a["scenario_id"], "supply_cut", {"fraction": 0.2})
    b = apply_new_or_get(api, hdr, "scn_sa01_w06_mild", "supply_cut", {"fraction": 0.2})
    ba = apply_new_or_get(api, hdr, b["scenario_id"], "tanker_down", {"tanker_id": "tk_01"})
    assert ab["scenario_id"] != ba["scenario_id"]
    assert ab["scenario_id"] == expected_id("scn_sa01_w06_mild", [
        {"type": "tanker_down", "params": {"tanker_id": "tk_01"}},
        {"type": "supply_cut", "params": {"fraction": 0.2}},
    ])  # fmt: skip
    assert ba["scenario_id"] == expected_id("scn_sa01_w06_mild", [
        {"type": "supply_cut", "params": {"fraction": 0.2}},
        {"type": "tanker_down", "params": {"tanker_id": "tk_01"}},
    ])  # fmt: skip
    for key in ("supply", "tankers", "heat"):  # same end state, different lineage
        assert ab[key] == ba[key]


def test_different_params_or_seeds_give_different_ids(api, hdr):
    ids = {
        apply_new_or_get(api, hdr, sid, "supply_cut", {"fraction": f})["scenario_id"]
        for sid in SEEDS
        for f in (0.15, 0.16)
    }
    assert len(ids) == 6


# --------------------------------------------------------------------------- persistence
def test_derived_scenarios_survive_an_app_restart(cfg, data_dir, hdr, api):
    child = apply_new_or_get(api, hdr, "scn_sa01_w06", "heat_alert", {"alert_level": 0})
    path = data_dir / "derived" / f"{child['scenario_id']}.json"
    assert path.is_file()
    with TestClient(create_app(cfg)) as fresh:
        assert get_scenario(fresh, hdr, child["scenario_id"]) == child
        again = disrupt(fresh, hdr, "scn_sa01_w06", "heat_alert", {"alert_level": 0})
        assert again.status_code == 200 and again.json() == child
        listed = fresh.get(f"{API}/scenarios?include_derived=true", headers=hdr).json()["scenarios"]
        assert child["scenario_id"] in [s["scenario_id"] for s in listed]


def test_seeds_are_never_modified_by_disruptions(api, hdr, data_dir):
    before = {p.name: p.read_bytes() for p in (data_dir / "scenarios").glob("*.json")}
    apply_new_or_get(api, hdr, "scn_sa01_w06", "supply_cut", {"fraction": 0.6})
    assert {p.name: p.read_bytes() for p in (data_dir / "scenarios").glob("*.json")} == before
    for sid in SEEDS:
        sc = get_scenario(api, hdr, sid)
        assert sc["parent_scenario_id"] is None and sc["disruptions_applied"] == []


# --------------------------------------------------------------------------- predictions of derived scenarios
def predictions(api, hdr, sid: str) -> dict:
    r = api.get(f"{API}/scenarios/{sid}/predictions", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    "dtype,params",
    [("tanker_down", {"tanker_id": "tk_03"}), ("supply_cut", {"fraction": 0.45}), ("heat_alert", {"alert_level": 3})],
)
def test_derived_predictions_have_null_run_mae(api, hdr, dtype, params):
    parent_pred = predictions(api, hdr, "scn_sa01_w06")
    assert isinstance(parent_pred["run_mae_litres"], float)  # the seed has hidden ground truth
    child = apply_new_or_get(api, hdr, "scn_sa01_w06", dtype, params)
    pred = predictions(api, hdr, child["scenario_id"])
    PredictionSet.model_validate(pred)
    assert_prediction_keys(pred)
    assert pred["scenario_id"] == child["scenario_id"]
    assert pred["run_mae_litres"] is None
    assert pred["is_simulation"] is True and pred["model_version"] == "xgb-sa-v1"
    assert [a["area_id"] for a in pred["areas"]] == [a["area_id"] for a in child["areas"]]
    for p in pred["areas"]:
        assert p["predicted_shortfall_litres"] == max(0, p["predicted_need_litres"] - p["expected_piped_litres"])
    if dtype in {"tanker_down", "supply_cut"}:  # neither changes any model feature
        assert [a["predicted_need_litres"] for a in pred["areas"]] == [a["predicted_need_litres"] for a in parent_pred["areas"]]
    else:  # a different heat alert changes the model inputs
        assert [a["predicted_need_litres"] for a in pred["areas"]] != [a["predicted_need_litres"] for a in parent_pred["areas"]]


# --------------------------------------------------------------------------- solving derived scenarios
def solve(api, hdr, sid: str, debt: dict | None = None, weights: dict | None = None) -> dict:
    r = api.post(
        f"{API}/plans/solve",
        json={"scenario_id": sid, "weights": weights or WEIGHTS, "debt_by_area": DEBT if debt is None else debt, "time_limit_s": 2.0},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("sid", SEEDS)
@pytest.mark.parametrize("tanker_id", ["tk_01", "tk_02", "tk_03", "tk_04"])
def test_solving_a_tanker_down_scenario_never_uses_that_tanker(api, hdr, sid, tanker_id):
    parent = get_scenario(api, hdr, sid)
    child = apply_new_or_get(api, hdr, sid, "tanker_down", {"tanker_id": tanker_id})
    plan = solve(api, hdr, child["scenario_id"])
    assert plan["scenario_id"] == child["scenario_id"]
    assert plan["prediction"]["run_mae_litres"] is None

    for a in plan["allocations"]:
        assert tanker_id not in [d["tanker_id"] for d in a["deliveries"]], a["area_id"]
    route = next(r for r in plan["routes"] if r["tanker_id"] == tanker_id)
    assert (route["status"], route["stops"], route["path"], route["load_litres"], route["route_km"]) == ("unavailable", [], [], 0, 0.0)
    assert plan["constraint_check"]["unavailable_tanker_ok"] is True and plan["constraint_check"]["violations"] == []
    lost = next(t["capacity_litres"] for t in parent["tankers"] if t["tanker_id"] == tanker_id)
    assert plan["totals"]["tanker_capacity_available_litres"] == sum(t["capacity_litres"] for t in parent["tankers"]) - lost
    assert plan["totals"]["allocated_litres"] <= plan["totals"]["tanker_capacity_available_litres"]
    assert_formulas(child, predictions(api, hdr, child["scenario_id"]), WEIGHTS, DEBT, plan)


@pytest.mark.parametrize("sid", SEEDS)
@pytest.mark.parametrize("fraction", [0.05, 0.3, 0.55, 0.9])
def test_solving_a_supply_cut_scenario_respects_the_cut_supply(api, hdr, sid, fraction):
    parent = get_scenario(api, hdr, sid)
    child = apply_new_or_get(api, hdr, sid, "supply_cut", {"fraction": fraction})
    plan = solve(api, hdr, child["scenario_id"])
    expected_supply = supply_after_cut(parent["supply"]["tanker_water_available_litres"], fraction)
    assert plan["totals"]["supply_available_litres"] == expected_supply
    assert plan["totals"]["allocated_litres"] <= expected_supply
    assert plan["prediction"]["run_mae_litres"] is None
    assert_formulas(child, predictions(api, hdr, child["scenario_id"]), WEIGHTS, DEBT, plan)


def test_supply_cut_plan_has_less_water_than_the_seed_plan(api, hdr):
    seed_plan = solve(api, hdr, "scn_sa01_w06")
    child = apply_new_or_get(api, hdr, "scn_sa01_w06", "supply_cut", {"fraction": 0.5})
    cut_plan = solve(api, hdr, child["scenario_id"])
    assert cut_plan["totals"]["allocated_litres"] < seed_plan["totals"]["allocated_litres"]
    assert cut_plan["totals"]["unmet_litres"] > seed_plan["totals"]["unmet_litres"]
    assert cut_plan["totals"]["shortfall_litres"] == seed_plan["totals"]["shortfall_litres"]


@pytest.mark.parametrize("level", [0, 1, 3])
def test_solving_a_heat_alert_scenario_works(api, hdr, level):
    child = apply_new_or_get(api, hdr, "scn_sa01_w06", "heat_alert", {"alert_level": level})
    plan = solve(api, hdr, child["scenario_id"])
    assert plan["scenario_id"] == child["scenario_id"] and plan["prediction"]["run_mae_litres"] is None
    assert plan["solver"]["status"] in {"optimal", "feasible", "time_limited", "heuristic"}
    assert_formulas(child, predictions(api, hdr, child["scenario_id"]), WEIGHTS, DEBT, plan)


def test_solving_a_chained_scenario_with_all_three_disruptions(api, hdr):
    c1 = apply_new_or_get(api, hdr, "scn_sa01_w06_severe", "tanker_down", {"tanker_id": "tk_01"})
    c2 = apply_new_or_get(api, hdr, c1["scenario_id"], "tanker_down", {"tanker_id": "tk_04"})
    c3 = apply_new_or_get(api, hdr, c2["scenario_id"], "supply_cut", {"fraction": 0.35})
    c4 = apply_new_or_get(api, hdr, c3["scenario_id"], "heat_alert", {"alert_level": 1})
    assert c4["scenario_id"].startswith("scn_sa01_w06_severe__d4_")
    assert [t["status"] for t in c4["tankers"] if t["tanker_id"] in {"tk_01", "tk_04"}] == ["unavailable", "unavailable"]

    plan = solve(api, hdr, c4["scenario_id"], debt={"ar_04": 7000, "ar_06": 3000})
    used = {d["tanker_id"] for a in plan["allocations"] for d in a["deliveries"]}
    assert used <= {"tk_02", "tk_03"}
    assert plan["totals"]["supply_available_litres"] == supply_after_cut(
        get_scenario(api, hdr, "scn_sa01_w06_severe")["supply"]["tanker_water_available_litres"], 0.35
    )
    assert_formulas(c4, predictions(api, hdr, c4["scenario_id"]), WEIGHTS, {"ar_04": 7000, "ar_06": 3000}, plan)


def test_derived_scenario_is_a_deep_copy_not_a_view(api, hdr):
    """Disrupting a scenario must not leak into the parent or into a sibling derived scenario."""
    before = copy.deepcopy(get_scenario(api, hdr, "scn_sa01_w06"))
    a = apply_new_or_get(api, hdr, "scn_sa01_w06", "tanker_down", {"tanker_id": "tk_04"})
    b = apply_new_or_get(api, hdr, "scn_sa01_w06", "tanker_down", {"tanker_id": "tk_01"})
    assert get_scenario(api, hdr, "scn_sa01_w06") == before
    assert [t["status"] for t in a["tankers"]] == ["available", "available", "available", "unavailable"]
    assert [t["status"] for t in b["tankers"]] == ["unavailable", "available", "available", "available"]
