"""Helpers for the property tests: seeded random scenarios plus an INDEPENDENT plan checker.

* ``random_case(seed)`` builds a valid (Scenario, PredictionSet, Weights, debt) quadruple from the CONTRACT 5.3
  example: 6-10 areas, 1-4 tankers (some unavailable), a valid symmetric road matrix (haversine x 1.3), varied
  supply / debts / route limits and weights that include the extremes 0 and the contract maximum.
* ``plan_problems(plan, ...)`` recomputes F-1, F-6..F-8, F-12, F-15 and the structural rules of the PlanResult
  from the scenario alone (it imports nothing from ``jp_a.domain.constraints``) and returns a list of messages;
  an empty list means the plan is sound.
"""
from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path
from typing import Any

from jp_a.domain.entities import PlanResult, PredictionSet, Scenario, Weights

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

ALLOWED_STATUSES = {"optimal", "feasible", "time_limited", "heuristic"}
ALLOWED_ENGINES = {"ortools-cp-sat", "greedy-fallback"}
UNSERVED_CODES = {"SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT", "TRAVEL_COST_TRADEOFF", "LOWER_RELATIVE_PRIORITY"}
SERVED_CODES = {"FULLY_SERVED", "PARTIALLY_SERVED", "NOT_SERVED", "NO_SHORTFALL"}

WEIGHT_MAX = {"urgency": 5.0, "debt": 5.0, "vulnerability": 5.0, "travel": 500.0}
DELIVERY_UNIT = 100

N_RANDOM = 50


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def road_matrix(nodes: dict[str, tuple[float, float]]) -> dict[str, dict[str, float]]:
    """Symmetric road distances (haversine x 1.3, 2 dp), zero diagonal."""
    matrix: dict[str, dict[str, float]] = {a: {} for a in nodes}
    for a in nodes:
        for b in nodes:
            matrix[a][b] = 0.0 if a == b else round(_haversine_km(*nodes[a], *nodes[b]) * 1.3, 2)
    return matrix


def _pick_weight(rng: random.Random, name: str) -> float:
    top = WEIGHT_MAX[name]
    roll = rng.random()
    if roll < 0.15:
        return 0.0
    if roll < 0.30:
        return top
    return round(rng.uniform(0.0, top), 2)


def random_weights(rng: random.Random) -> Weights:
    return Weights(**{name: _pick_weight(rng, name) for name in WEIGHT_MAX})


def random_case(
    seed: int,
    *,
    n_areas: int | None = None,
    n_tankers: int | None = None,
    unavailable: int | None = None,
    supply_ratio: float | None = None,
    radius_km: float | None = None,
    max_route_km: float | None = None,
    weights: Weights | None = None,
) -> tuple[Scenario, PredictionSet, Weights, dict[str, int]]:
    """A valid random scenario. Explicit keyword arguments pin a dimension, the rest is drawn from ``seed``."""
    rng = random.Random(1000 + seed)
    base = _fixture("contract_scenario_example")
    pred_template = _fixture("contract_prediction_example")
    template = base["areas"][0]
    top_features = pred_template["areas"][0]["top_features"]
    depot = (base["depot"]["lat"], base["depot"]["lon"])
    km_lat = 111.0
    km_lon = 111.0 * math.cos(math.radians(depot[0]))

    n_areas = n_areas if n_areas is not None else rng.randint(6, 10)
    n_tankers = n_tankers if n_tankers is not None else rng.randint(1, 4)
    radius = radius_km if radius_km is not None else rng.choice([3.0, 6.0, 10.0, 14.0])

    areas: list[dict[str, Any]] = []
    preds: list[dict[str, Any]] = []
    debt: dict[str, int] = {}
    nodes = {base["depot"]["depot_id"]: depot}
    for n in range(1, n_areas + 1):
        area_id = f"ar_{n:02d}"
        angle = rng.uniform(0, 2 * math.pi)
        r = radius * math.sqrt(rng.uniform(0.02, 1.0))
        lat = round(depot[0] + r * math.sin(angle) / km_lat, 4)
        lon = round(depot[1] + r * math.cos(angle) / km_lon, 4)
        nodes[area_id] = (lat, lon)
        need = rng.randrange(8_000, 60_000, 50)
        piped = int(need * rng.uniform(0.45, 1.05))  # > need -> zero shortfall for some areas
        area = copy.deepcopy(template)
        area.update(
            area_id=area_id,
            name=f"Ward {n:02d} (synthetic)",
            lat=lat,
            lon=lon,
            population=rng.randint(4_000, 25_000),
            vulnerability_index=round(rng.uniform(0.0, 1.0), 2),
            vulnerability_available=rng.random() > 0.15,
            heat_exposure=round(rng.uniform(0.0, 1.0), 2),
            expected_piped_litres=piped,
        )
        areas.append(area)
        preds.append(
            {
                "area_id": area_id,
                "predicted_need_litres": need,
                "expected_piped_litres": piped,
                "predicted_shortfall_litres": max(0, need - piped),
                "baseline_last_need_litres": max(0, need - rng.randint(0, 2_000)),
                "top_features": copy.deepcopy(top_features),
            }
        )
        if rng.random() < 0.65:
            debt[area_id] = rng.randrange(0, 20_000, 100)

    if unavailable is None:
        unavailable = rng.randint(0, max(0, n_tankers - 1)) if rng.random() < 0.6 else 0
    unavailable = min(unavailable, n_tankers)
    route_limit = max_route_km if max_route_km is not None else rng.choice([10.0, 20.0, 35.0, 60.0, 60.0])
    tankers = [
        {
            "tanker_id": f"tk_{n:02d}",
            "capacity_litres": rng.choice([10_000, 11_000, 12_000]),
            "status": "unavailable" if n > n_tankers - unavailable else "available",
            "max_route_km": route_limit,
        }
        for n in range(1, n_tankers + 1)
    ]

    total_shortfall = sum(p["predicted_shortfall_litres"] for p in preds)
    ratio = supply_ratio if supply_ratio is not None else rng.choice([0.0, 0.2, 0.4, 0.55, 0.8, 1.0, 1.5])
    supply = int(total_shortfall * ratio) // DELIVERY_UNIT * DELIVERY_UNIT
    base.update(
        scenario_id=f"scn_property_{seed}",
        areas=areas,
        tankers=tankers,
        supply={"tanker_water_available_litres": supply},
        distance_km=road_matrix(nodes),
    )
    base["heat"] = {**base["heat"], "alert_level": rng.randint(0, 3)}
    pred_template.update(scenario_id=base["scenario_id"], areas=preds)
    chosen = weights if weights is not None else random_weights(rng)
    return Scenario.model_validate(base), PredictionSet.model_validate(pred_template), chosen, debt


def stable_dump(plan: PlanResult) -> dict[str, Any]:
    """Plan JSON minus the fields that legitimately differ between two identical solves."""
    data = plan.model_dump(mode="json")
    data.pop("result_id")
    data.pop("generated_at")
    data["solver"].pop("solve_time_ms")
    data["prediction"].pop("latency_ms")
    return data


# --------------------------------------------------------------------------- independent checker
def count_violations(plan: PlanResult, scenario: Scenario) -> int:
    """F-15 recomputed from scratch: water cap, tanker capacity, route limit, unavailable tanker."""
    data = plan.model_dump(mode="json")
    tankers = {t.tanker_id: t for t in scenario.tankers}
    dist = scenario.distance_km
    count = 0
    allocated = sum(a["allocated_litres"] for a in data["allocations"])
    if allocated > scenario.supply.tanker_water_available_litres:
        count += 1
    loads: dict[str, int] = {}
    for a in data["allocations"]:
        for d in a["deliveries"]:
            loads[d["tanker_id"]] = loads.get(d["tanker_id"], 0) + d["litres"]
            tanker = tankers.get(d["tanker_id"])
            if tanker is None or tanker.status != "available":
                count += 1
    for tanker_id, load in loads.items():
        tanker = tankers.get(tanker_id)
        if tanker is not None and load > tanker.capacity_litres:
            count += 1
    for r in data["routes"]:
        km = sum(dist[x][y] for x, y in zip(r["path"], r["path"][1:], strict=False))
        tanker = tankers[r["tanker_id"]]
        if km > tanker.max_route_km + 0.01:
            count += 1
    return count


def plan_problems(
    plan: PlanResult, scenario: Scenario, prediction: PredictionSet, weights: Weights, debt: dict[str, int]
) -> list[str]:
    """Every defect found by independent recomputation (empty list = plan is sound)."""
    out: list[str] = []
    data = plan.model_dump(mode="json")
    try:
        PlanResult.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        out.append(f"strict PlanResult parse failed: {exc}")
    if count_violations(plan, scenario) != 0:
        out.append(f"independent F-15 count is {count_violations(plan, scenario)}")
    cc = data["constraint_check"]
    if cc["violations"] or not (cc["water_cap_ok"] and cc["capacity_ok"] and cc["route_limit_ok"] and cc["unavailable_tanker_ok"]):
        out.append(f"constraint_check reports violations: {cc}")
    if data["solver"]["status"] not in ALLOWED_STATUSES or data["solver"]["engine"] not in ALLOWED_ENGINES:
        out.append(f"unexpected solver block {data['solver']}")
    if data["strategy"] != "jalpriority" or data["scenario_id"] != scenario.scenario_id:
        out.append("strategy / scenario_id mismatch")

    preds = {p.area_id: p for p in prediction.areas}
    tankers = {t.tanker_id: t for t in scenario.tankers}
    supply = scenario.supply.tanker_water_available_litres
    allocs = data["allocations"]
    if sorted(a["area_id"] for a in allocs) != sorted(a.area_id for a in scenario.areas):
        out.append("allocations do not cover each area exactly once")

    for a in allocs:
        p = preds[a["area_id"]]
        shortfall = max(0, p.predicted_need_litres - p.expected_piped_litres)  # F-1
        got = a["allocated_litres"]
        if a["predicted_shortfall_litres"] != shortfall:
            out.append(f"{a['area_id']}: shortfall {a['predicted_shortfall_litres']} != F-1 {shortfall}")
        cap = -(-shortfall // DELIVERY_UNIT) * DELIVERY_UNIT  # the shortfall rounded up to whole 100 L units
        if not 0 <= got <= cap:
            out.append(f"{a['area_id']}: allocated {got} outside [0, {cap}]")
        if got % DELIVERY_UNIT:
            out.append(f"{a['area_id']}: allocated {got} is not a multiple of 100")
        if got != sum(d["litres"] for d in a["deliveries"]):
            out.append(f"{a['area_id']}: allocated {got} != sum of deliveries")
        if any(d["litres"] % DELIVERY_UNIT or d["litres"] <= 0 for d in a["deliveries"]):
            out.append(f"{a['area_id']}: delivery not a positive multiple of 100")
        if a["unmet_litres"] != max(0, shortfall - got):  # F-6
            out.append(f"{a['area_id']}: unmet {a['unmet_litres']} != {max(0, shortfall - got)}")
        expected_cov = 1.0 if shortfall == 0 else round(min(1.0, got / shortfall), 4)  # F-7
        if a["coverage_ratio"] != expected_cov:
            out.append(f"{a['area_id']}: coverage {a['coverage_ratio']} != {expected_cov}")
        if a["is_unserved_remainder"] != (a["unmet_litres"] > 0):
            out.append(f"{a['area_id']}: is_unserved_remainder inconsistent")
        if not a["reasons"] or not a["reason_text"].strip():
            out.append(f"{a['area_id']}: empty reasons / reason_text")
        codes = [r["code"] for r in a["reasons"]]
        if len([c for c in codes if c in SERVED_CODES]) != 1:
            out.append(f"{a['area_id']}: need exactly one served-state reason, got {codes}")
        if a["unmet_litres"] > 0 and not (set(codes) & UNSERVED_CODES):
            out.append(f"{a['area_id']}: unmet > 0 but no unserved cause in {codes}")
        if a["unmet_litres"] == 0 and set(codes) & UNSERVED_CODES:
            out.append(f"{a['area_id']}: no unmet litres but an unserved cause / LOWER_RELATIVE_PRIORITY in {codes}")

    allocated_total = sum(a["allocated_litres"] for a in allocs)
    if allocated_total > supply:
        out.append(f"allocated {allocated_total} > supply {supply}")
    totals = data["totals"]
    if totals["allocated_litres"] != allocated_total or totals["unmet_litres"] != sum(a["unmet_litres"] for a in allocs):
        out.append(f"totals inconsistent with allocations: {totals}")  # F-12

    unserved = data["unserved"]
    expected_unserved = [a["area_id"] for a in allocs if a["unmet_litres"] > 0]
    if [u["area_id"] for u in unserved] != expected_unserved:
        out.append(f"unserved list {[u['area_id'] for u in unserved]} != areas with unmet > 0 {expected_unserved}")
    by_area = {a["area_id"]: a for a in allocs}
    for u in unserved:
        if u["unmet_litres"] != by_area[u["area_id"]]["unmet_litres"] or u["unmet_litres"] <= 0:
            out.append(f"unserved {u['area_id']}: unmet mismatch")
        # Sub-100 L residue is never listed as a priority decision: an optimal plan with spare water, capacity
        # and route would always add the last (rounded-up) unit, so a small remainder must have a hard cause.
        if 0 < u["unmet_litres"] < DELIVERY_UNIT and data["solver"]["status"] == "optimal":
            if u["reason_code"] == "LOWER_RELATIVE_PRIORITY":
                out.append(f"unserved {u['area_id']}: {u['unmet_litres']} L rounding residue listed as lower priority")

    if sorted(r["tanker_id"] for r in data["routes"]) != sorted(tankers):
        out.append("routes do not list every tanker exactly once")
    travel = 0.0
    for r in data["routes"]:
        tanker = tankers[r["tanker_id"]]
        km = round(sum(scenario.distance_km[x][y] for x, y in zip(r["path"], r["path"][1:], strict=False)), 2)  # F-8
        travel += km
        if r["route_km"] != km:
            out.append(f"{r['tanker_id']}: route_km {r['route_km']} != matrix {km}")
        if km > tanker.max_route_km + 0.01:
            out.append(f"{r['tanker_id']}: route {km} km > max {tanker.max_route_km}")
        load = sum(s["litres"] for s in r["stops"])
        if r["load_litres"] != load or load > tanker.capacity_litres:
            out.append(f"{r['tanker_id']}: load {r['load_litres']} / {load} vs capacity {tanker.capacity_litres}")
        if tanker.status == "unavailable" and (r["stops"] or r["status"] != "unavailable"):
            out.append(f"{r['tanker_id']}: unavailable tanker has stops or wrong status")
        if r["stops"]:
            depot = scenario.depot.depot_id
            if r["path"] != [depot, *[s["area_id"] for s in r["stops"]], depot]:
                out.append(f"{r['tanker_id']}: path does not start/end at the depot through its stops")
            if [s["stop_order"] for s in r["stops"]] != list(range(1, len(r["stops"]) + 1)):
                out.append(f"{r['tanker_id']}: stop_order is not 1..n")
    if abs(totals["travel_km"] - travel) > 0.011:
        out.append(f"travel_km {totals['travel_km']} != {round(travel, 2)}")  # F-14
    return out
