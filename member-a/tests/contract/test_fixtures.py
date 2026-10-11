"""M1: all 9 CONTRACT section 10 fixtures parse under the strict (extra=forbid) models; section 5.11 worked check.

OWNER: LEAD (read-only for agents; QA extends other files in tests/contract/)."""
from __future__ import annotations

import copy
import itertools

import pytest
from pydantic import ValidationError

from jp_a.domain.entities import (
    Event,
    Metrics,
    ModelInfo,
    PlanRecord,
    PlanResult,
    PredictionSet,
    Scenario,
    ScenarioSummary,
    SolveRequest,
)

FIXTURE_MODELS = {
    "contract_scenario_example": Scenario,
    "contract_scenario_summary_example": ScenarioSummary,
    "contract_prediction_example": PredictionSet,
    "contract_model_info_example": ModelInfo,
    "contract_solve_request_example": SolveRequest,
    "contract_plan_result_example": PlanResult,
    "contract_metrics_example": Metrics,
    "contract_plan_record_example": PlanRecord,
    "contract_event_example": Event,
}


@pytest.mark.parametrize("name,model", FIXTURE_MODELS.items())
def test_fixture_parses_strictly(load_fixture, name, model):
    data = load_fixture(name)
    obj = model.model_validate(data)
    # round-trip equality: no field silently defaulted / dropped
    assert obj.model_dump(mode="json") == data


@pytest.mark.parametrize("name,model", FIXTURE_MODELS.items())
def test_unknown_field_is_rejected(load_fixture, name, model):
    data = load_fixture(name)
    data["surprise_field"] = 1
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize("name,model", [(n, m) for n, m in FIXTURE_MODELS.items() if n != "contract_solve_request_example"])
def test_missing_field_is_rejected(load_fixture, name, model):
    data = load_fixture(name)
    data.pop(next(k for k in data if k != "schema_version"))
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_plan_record_embeds_full_plan_result(load_fixture):
    rec = load_fixture("contract_plan_record_example")
    assert rec["result"] == load_fixture("contract_plan_result_example")
    assert "comment" not in rec["result"]


def test_scenario_summary_label_and_ids(load_fixture):
    s = ScenarioSummary.model_validate(load_fixture("contract_scenario_summary_example"))
    assert s.label.startswith("Main demo") and s.scenario_id == "scn_sa01_w06"


@pytest.mark.parametrize(
    "sid,ok",
    [
        ("scn_sa01_w06", True),
        ("scn_sa01_w06_severe", True),
        ("scn_sa01_w06__d1_3f9a2c", True),
        ("scn_sa01_w06__d12_abcdef", True),
        ("scn_sa01__w06", False),
        ("scn_sa01_w06__d1_3F9A2C", False),
        ("scn_sa01_w06__d1_3f9a2", False),
        ("SCN_sa01", False),
        ("sa01_w06", False),
        ("scn_", False),
    ],
)
def test_scenario_id_patterns(sid, ok):
    from jp_a.domain.entities import is_valid_scenario_id

    assert is_valid_scenario_id(sid) is ok


def test_weights_ranges():
    from jp_a.domain.entities import Weights

    assert Weights().model_dump() == {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
    for bad in ({"urgency": 5.01}, {"debt": -0.1}, {"vulnerability": 6}, {"travel": 500.5}):
        with pytest.raises(ValidationError):
            Weights(**bad)
    Weights(urgency=5, debt=0, vulnerability=5, travel=500)


def test_datetimes_must_be_utc_millisecond_z(load_fixture):
    data = load_fixture("contract_event_example")
    for bad in ("2026-10-10T09:14:58Z", "2026-10-10T09:14:58.12Z", "2026-10-10T09:14:58.120+00:00", "2026-10-10 09:14:58.120Z"):
        d = copy.deepcopy(data)
        d["ts"] = bad
        with pytest.raises(ValidationError):
            Event.model_validate(d)


def test_top_features_exactly_three(load_fixture):
    data = load_fixture("contract_prediction_example")
    data["areas"][0]["top_features"] = data["areas"][0]["top_features"][:2]
    with pytest.raises(ValidationError):
        PredictionSet.model_validate(data)


# --------------------------------------------------------------------------- section 5.11 worked check (independent re-computation)
def _formulas(load_fixture):
    scn = load_fixture("contract_scenario_example")
    pred = {a["area_id"]: a for a in load_fixture("contract_prediction_example")["areas"]}
    req = load_fixture("contract_solve_request_example")
    w, debt = req["weights"], req["debt_by_area"]
    dmax = max(1, max(debt.get(a["area_id"], 0) for a in scn["areas"]))
    out = {}
    for a in scn["areas"]:
        i = a["area_id"]
        sf = max(0, pred[i]["predicted_need_litres"] - pred[i]["expected_piped_litres"])
        assert sf == pred[i]["predicted_shortfall_litres"]  # F-1
        need = pred[i]["predicted_need_litres"]
        u = sf / need if need > 0 else 0.0  # F-2
        dh = debt.get(i, 0) / dmax  # F-3
        v = a["vulnerability_index"] if a["vulnerability_available"] else 0.0  # F-4
        p = round(1 + w["urgency"] * u + w["debt"] * dh + w["vulnerability"] * v, 4)  # F-5
        out[i] = dict(sf=sf, U=u, Dh=dh, V=v, p=p)
    return scn, out, debt


def test_priority_formulas_match_plan_fixture(load_fixture):
    _, f, _ = _formulas(load_fixture)
    plan = load_fixture("contract_plan_result_example")
    assert f["ar_01"]["p"] == 1.4093 and f["ar_02"]["p"] == 2.2144 and f["ar_03"]["p"] == 2.6857
    for alloc in plan["allocations"]:
        x = f[alloc["area_id"]]
        assert alloc["priority_weight"] == x["p"]
        assert alloc["urgency"] == round(x["U"], 4)
        assert alloc["debt_normalized"] == round(x["Dh"], 4)
        assert alloc["vulnerability_used"] == x["V"]
        assert alloc["predicted_shortfall_litres"] == x["sf"]
        # F-6 / F-7
        assert alloc["unmet_litres"] == max(0, x["sf"] - alloc["allocated_litres"])
        cov = 1.0 if x["sf"] == 0 else round(min(1, alloc["allocated_litres"] / x["sf"]), 4)
        assert alloc["coverage_ratio"] == cov


def test_route_km_f8_and_totals(load_fixture):
    scn = load_fixture("contract_scenario_example")
    plan = load_fixture("contract_plan_result_example")
    d = scn["distance_km"]
    total = 0.0
    for r in plan["routes"]:
        km = round(sum(d[a][b] for a, b in itertools.pairwise(r["path"])), 2) if r["path"] else 0.0
        assert abs(km - r["route_km"]) < 0.01
        total += km
    assert round(total, 2) == plan["totals"]["travel_km"] == 37.2
    t = plan["totals"]
    assert t["allocated_litres"] == sum(a["allocated_litres"] for a in plan["allocations"]) == 20000
    assert t["unmet_litres"] == sum(a["unmet_litres"] for a in plan["allocations"]) == 9000
    assert t["shortfall_litres"] == sum(a["predicted_shortfall_litres"] for a in plan["allocations"]) == 29000


def test_metrics_worked_check_pwu_and_debt(load_fixture):
    _, f, debt = _formulas(load_fixture)
    plan = load_fixture("contract_plan_result_example")
    metrics = load_fixture("contract_metrics_example")
    unmet = {a["area_id"]: a["unmet_litres"] for a in plan["allocations"]}
    pwu = round(sum(f[i]["p"] * unmet[i] for i in unmet), 1)  # F-9
    assert pwu == metrics["priority_weighted_unmet"] == 12683.7
    lam = 0.3
    remaining = sum(round((1 - lam) * debt.get(i, 0) + unmet[i]) for i in unmet)  # F-10 / F-11
    assert remaining == metrics["deprivation_debt_remaining_litres"] == 23000
    assert metrics["total_unmet_litres"] == sum(unmet.values())  # F-12
    covs = [a["coverage_ratio"] for a in plan["allocations"] if a["predicted_shortfall_litres"] > 0]  # F-13
    assert min(covs) == metrics["min_coverage_ratio"] == 0.3571
    assert metrics["total_travel_km"] == plan["totals"]["travel_km"]  # F-14


def test_ledger_seeding_check_from_history(load_fixture):
    """CONTRACT 5.11: history in 5.3 gives debt after window 5 of {0, 8000, 12000} with lambda = 0.3."""
    scn = load_fixture("contract_scenario_example")
    lam = 0.3
    want = {"ar_01": 0, "ar_02": 8000, "ar_03": 12000}
    for a in scn["areas"]:
        d = 0
        for h in a["history"]:
            d = round((1 - lam) * d + max(0, h["need_litres"] - h["received_litres"]))
        assert d == want[a["area_id"]], a["area_id"]


def test_f15_zero_violations_on_fixture(load_fixture):
    scn = load_fixture("contract_scenario_example")
    plan = load_fixture("contract_plan_result_example")
    assert plan["constraint_check"]["violations"] == []
    supply = scn["supply"]["tanker_water_available_litres"]
    assert sum(a["allocated_litres"] for a in plan["allocations"]) <= supply
    tk = {t["tanker_id"]: t for t in scn["tankers"]}
    for r in plan["routes"]:
        assert r["load_litres"] <= tk[r["tanker_id"]]["capacity_litres"]
        assert r["route_km"] <= tk[r["tanker_id"]]["max_route_km"] + 0.01
        if tk[r["tanker_id"]]["status"] == "unavailable":
            assert r["stops"] == [] and r["status"] == "unavailable"
