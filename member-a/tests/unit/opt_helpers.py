"""Test helpers for the optimizer tests (test_opt_*.py).

* ``golden_inputs()``: the CONTRACT 5.3 scenario, 5.4 predictions and the 6.6 solve request.
* ``make_case(seed, ...)``: seeded synthetic scenarios built by perturbing the contract example (valid ids,
  symmetric road-distance matrix) together with a consistent PredictionSet and a debt map.
* ``assert_plan_invariants(...)``: an independent re-implementation of F-1..F-9, F-12..F-15 and the
  PlanResult structural rules, used against every plan the tests produce.
"""
from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path
from typing import Any

from jp_a.domain.entities import PlanResult, PredictionSet, Scenario, SolveRequest, Weights

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

REASON_PARAMS: dict[str, set[str]] = {
    "HIGH_PREDICTED_SHORTFALL": {"predicted_shortfall_litres", "urgency"},
    "HIGH_DEBT": {"debt_litres"},
    "HIGH_VULNERABILITY": {"vulnerability_index"},
    "HEAT_ALERT": {"alert_level"},
    "LOWER_RELATIVE_PRIORITY": {"priority_weight"},
    "FULLY_SERVED": {"allocated_litres"},
    "PARTIALLY_SERVED": {"allocated_litres", "predicted_shortfall_litres"},
    "NOT_SERVED": {"predicted_shortfall_litres"},
    "NO_SHORTFALL": set(),
    "SUPPLY_EXHAUSTED": {"supply_available_litres", "unmet_litres"},
    "TANKER_CAPACITY_LIMIT": {"tanker_capacity_available_litres", "unmet_litres"},
    "ROUTE_LIMIT": {"distance_from_depot_km"},
    "TRAVEL_COST_TRADEOFF": {"distance_from_depot_km", "travel_weight"},
    "TIME_LIMITED_SOLUTION": {"time_limit_s"},
}
SERVED = {"FULLY_SERVED", "PARTIALLY_SERVED", "NOT_SERVED", "NO_SHORTFALL"}
UNSERVED_CODES = {"SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT", "TRAVEL_COST_TRADEOFF", "LOWER_RELATIVE_PRIORITY"}
VOLATILE = ("result_id", "generated_at")


def load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / (name if name.endswith(".json") else f"{name}.json")
    return json.loads(path.read_text(encoding="utf-8"))


def golden_inputs() -> tuple[Scenario, PredictionSet, Weights, dict[str, int]]:
    scenario = Scenario.model_validate(load_fixture("contract_scenario_example"))
    prediction = PredictionSet.model_validate(load_fixture("contract_prediction_example"))
    request = SolveRequest.model_validate(load_fixture("contract_solve_request_example"))
    return scenario, prediction, request.weights, dict(request.debt_by_area)


def stable_dump(plan: PlanResult) -> dict[str, Any]:
    """Plan JSON without the fields allowed to differ between identical solves."""
    data = plan.model_dump(mode="json")
    for key in VOLATILE:
        data.pop(key)
    data["solver"].pop("solve_time_ms")
    return data


def scenario_dict(scenario: Scenario) -> dict[str, Any]:
    return copy.deepcopy(scenario.model_dump(mode="json"))


# --------------------------------------------------------------------------- synthetic cases
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def road_matrix(nodes: dict[str, tuple[float, float]]) -> dict[str, dict[str, float]]:
    """Symmetric road distances (haversine x 1.3, 2 dp) with a zero diagonal."""
    ids = list(nodes)
    matrix: dict[str, dict[str, float]] = {a: {} for a in ids}
    for a in ids:
        for b in ids:
            matrix[a][b] = 0.0 if a == b else round(_haversine_km(*nodes[a], *nodes[b]) * 1.3, 2)
    return matrix


def make_case(
    seed: int,
    n_areas: int = 10,
    n_tankers: int = 4,
    *,
    supply_ratio: float = 0.55,
    unavailable: int = 0,
    radius_km: float = 6.0,
    max_route_km: float = 60.0,
) -> tuple[Scenario, PredictionSet, dict[str, int]]:
    """A random but valid (scenario, predictions, debt) triple derived from the contract example."""
    rng = random.Random(seed)
    base = load_fixture("contract_scenario_example")
    template = base["areas"][0]
    depot = (base["depot"]["lat"], base["depot"]["lon"])
    km_per_deg_lat = 111.0
    km_per_deg_lon = 111.0 * math.cos(math.radians(depot[0]))

    areas, preds, debt = [], [], {}
    nodes = {base["depot"]["depot_id"]: depot}
    for n in range(1, n_areas + 1):
        area_id = f"ar_{n:02d}"
        angle, radius = rng.uniform(0, 2 * math.pi), radius_km * math.sqrt(rng.uniform(0.02, 1.0))
        lat = round(depot[0] + radius * math.sin(angle) / km_per_deg_lat, 4)
        lon = round(depot[1] + radius * math.cos(angle) / km_per_deg_lon, 4)
        nodes[area_id] = (lat, lon)
        need = rng.randrange(8_000, 60_000, 50)
        piped = int(need * rng.uniform(0.55, 0.95))
        area = copy.deepcopy(template)
        area.update(
            area_id=area_id,
            name=f"Ward {n:02d} (synthetic)",
            lat=lat,
            lon=lon,
            population=rng.randint(4_000, 25_000),
            vulnerability_index=round(rng.uniform(0.1, 0.95), 2),
            vulnerability_available=rng.random() > 0.1,
            heat_exposure=round(rng.uniform(0.3, 0.95), 2),
            expected_piped_litres=piped,
        )
        areas.append(area)
        preds.append(
            {
                "area_id": area_id,
                "predicted_need_litres": need,
                "expected_piped_litres": piped,
                "predicted_shortfall_litres": max(0, need - piped),
                "baseline_last_need_litres": need - rng.randint(0, 2_000),
                "top_features": copy.deepcopy(load_fixture("contract_prediction_example")["areas"][0]["top_features"]),
            }
        )
        if rng.random() < 0.6:
            debt[area_id] = rng.randrange(0, 15_000, 100)

    tankers = []
    for n in range(1, n_tankers + 1):
        tankers.append(
            {
                "tanker_id": f"tk_{n:02d}",
                "capacity_litres": rng.choice([10_000, 11_000, 12_000]),
                "status": "unavailable" if n > n_tankers - unavailable else "available",
                "max_route_km": max_route_km,
            }
        )
    total_shortfall = sum(p["predicted_shortfall_litres"] for p in preds)
    base.update(
        scenario_id=f"scn_synthetic_{seed}",
        areas=areas,
        tankers=tankers,
        supply={"tanker_water_available_litres": int(total_shortfall * supply_ratio) // 100 * 100},
        distance_km=road_matrix(nodes),
    )
    prediction = load_fixture("contract_prediction_example")
    prediction.update(scenario_id=base["scenario_id"], areas=preds)
    return Scenario.model_validate(base), PredictionSet.model_validate(prediction), debt


# --------------------------------------------------------------------------- independent checks
def _rank_key(a: dict[str, Any]) -> tuple:
    return (-a["priority_weight"], -a["predicted_shortfall_litres"], a["area_id"])


def assert_plan_invariants(
    plan: PlanResult, scenario: Scenario, prediction: PredictionSet, weights: Weights, debt: dict[str, int]
) -> None:
    data = plan.model_dump(mode="json")
    PlanResult.model_validate(data)  # strict round trip
    assert data["strategy"] == "jalpriority"
    assert (data["scenario_id"], data["service_area_id"], data["window_index"]) == (
        scenario.scenario_id,
        scenario.service_area_id,
        scenario.window_index,
    )
    assert data["weights"] == weights.model_dump(mode="json")

    preds = {p.area_id: p for p in prediction.areas}
    areas = {a.area_id: a for a in scenario.areas}
    tankers = {t.tanker_id: t for t in scenario.tankers}
    depot = scenario.depot.depot_id
    dist = scenario.distance_km
    max_debt = max([debt.get(a, 0) for a in areas] + [0])

    allocs = data["allocations"]
    assert sorted(a["area_id"] for a in allocs) == sorted(areas)
    assert [a["rank"] for a in allocs] == list(range(1, len(allocs) + 1))
    assert allocs == sorted(allocs, key=_rank_key)

    pwu = 0.0
    for a in allocs:
        p, area = preds[a["area_id"]], areas[a["area_id"]]
        sf = max(0, p.predicted_need_litres - p.expected_piped_litres)  # F-1
        assert a["predicted_shortfall_litres"] == sf
        u = sf / p.predicted_need_litres if p.predicted_need_litres > 0 else 0.0  # F-2
        d = debt.get(a["area_id"], 0)
        d_hat = d / max(1, max_debt)  # F-3
        v = area.vulnerability_index if area.vulnerability_available else 0.0  # F-4
        prio = round(1 + weights.urgency * u + weights.debt * d_hat + weights.vulnerability * v, 4)  # F-5
        assert a["urgency"] == round(u, 4)
        assert a["debt_normalized"] == round(d_hat, 4)
        assert a["vulnerability_used"] == round(v, 4)
        assert a["priority_weight"] == prio
        assert a["debt_litres_used"] == d
        assert a["allocated_litres"] == sum(x["litres"] for x in a["deliveries"])
        assert 0 <= a["allocated_litres"] <= sf
        assert a["allocated_litres"] % 100 == 0
        unmet = max(0, sf - a["allocated_litres"])  # F-6
        assert a["unmet_litres"] == unmet
        assert a["is_unserved_remainder"] == (unmet > 0)
        assert a["coverage_ratio"] == (1.0 if sf == 0 else round(min(1, a["allocated_litres"] / sf), 4))  # F-7
        pwu += prio * unmet
        _assert_reasons(a, sf)

    routes = data["routes"]
    assert [r["tanker_id"] for r in routes] == sorted(tankers)
    stop_index = {}
    travel = 0.0
    for r in routes:
        t = tankers[r["tanker_id"]]
        assert (r["capacity_litres"], r["max_route_km"]) == (t.capacity_litres, t.max_route_km)
        legs = zip(r["path"], r["path"][1:], strict=False)
        km = round(sum(dist[x][y] for x, y in legs), 2) if r["path"] else 0.0  # F-8
        assert r["route_km"] == km
        assert km <= t.max_route_km + 0.01
        travel += km
        assert r["load_litres"] == sum(s["litres"] for s in r["stops"]) <= t.capacity_litres
        if t.status == "unavailable":
            assert r["status"] == "unavailable" and r["stops"] == [] and r["path"] == [] and r["load_litres"] == 0
        elif not r["stops"]:
            assert r["status"] == "idle" and r["path"] == [] and r["route_km"] == 0.0
        else:
            assert r["status"] == "assigned"
            assert [s["stop_order"] for s in r["stops"]] == list(range(1, len(r["stops"]) + 1))
            assert r["path"] == [depot, *[s["area_id"] for s in r["stops"]], depot]
            assert all(s["litres"] > 0 for s in r["stops"])
            for s in r["stops"]:
                stop_index[(r["tanker_id"], s["area_id"])] = (s["stop_order"], s["litres"])
    deliveries = {
        (d["tanker_id"], a["area_id"]): (d["stop_order"], d["litres"]) for a in allocs for d in a["deliveries"]
    }
    assert deliveries == stop_index

    totals = data["totals"]
    assert totals["supply_available_litres"] == scenario.supply.tanker_water_available_litres
    assert totals["tanker_capacity_available_litres"] == sum(
        t.capacity_litres for t in scenario.tankers if t.status == "available"
    )
    assert totals["shortfall_litres"] == sum(a["predicted_shortfall_litres"] for a in allocs)
    assert totals["allocated_litres"] == sum(a["allocated_litres"] for a in allocs) <= totals["supply_available_litres"]
    assert totals["unmet_litres"] == sum(a["unmet_litres"] for a in allocs)  # F-12
    assert totals["travel_km"] == round(travel, 2)  # F-14
    assert round(pwu, 1) >= 0  # F-9 is well defined on every plan

    unserved = data["unserved"]
    assert [u["area_id"] for u in unserved] == [a["area_id"] for a in allocs if a["unmet_litres"] > 0]
    for u in unserved:
        alloc = next(a for a in allocs if a["area_id"] == u["area_id"])
        assert u["unmet_litres"] == alloc["unmet_litres"]
        assert u["reason_code"] in UNSERVED_CODES
        assert u["reason_code"] in [r["code"] for r in alloc["reasons"]]

    cc = data["constraint_check"]
    assert cc == {
        "water_cap_ok": True,
        "capacity_ok": True,
        "route_limit_ok": True,
        "unavailable_tanker_ok": True,
        "violations": [],
    }


def _assert_reasons(alloc: dict[str, Any], sf: int) -> None:
    codes = [r["code"] for r in alloc["reasons"]]
    assert codes and len(codes) == len(set(codes))
    for r in alloc["reasons"]:
        assert r["code"] in REASON_PARAMS
        assert set(r["params"]) == REASON_PARAMS[r["code"]]
    served = [c for c in codes if c in SERVED]
    assert len(served) == 1
    expected = (
        "NO_SHORTFALL"
        if sf == 0
        else "NOT_SERVED"
        if alloc["allocated_litres"] == 0
        else "FULLY_SERVED"
        if alloc["unmet_litres"] == 0
        else "PARTIALLY_SERVED"
    )
    assert served[0] == expected
    after_served = codes[codes.index(served[0]) + 1 :]
    causes = [c for c in codes if c in UNSERVED_CODES - {"LOWER_RELATIVE_PRIORITY"}]
    if alloc["unmet_litres"] > 0:
        assert len(causes) <= 1
        assert causes or "LOWER_RELATIVE_PRIORITY" in codes
    else:
        assert not causes
    assert set(after_served) <= UNSERVED_CODES | {"TIME_LIMITED_SOLUTION"}
    assert alloc["reason_text"].strip()
