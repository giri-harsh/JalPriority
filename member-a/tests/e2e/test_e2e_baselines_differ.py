"""FCFS and nearest-first must be visibly DIFFERENT strategies on every seed, and JalPriority must beat both.

Both baselines are implemented here exactly per the B5 rules:
  * FCFS: order by request_time (ties by area_id); nearest-first: by depot road distance (ties by area_id);
  * target = requested_litres floored to 100 L (0 -> skipped);
  * fill tankers first-fit by tanker_id (a full tanker continues the same area on the next one), stop at supply S;
  * route check before adding a stop: the route in service order incl. the return to the depot stays <= max_route_km,
    otherwise try the next tanker; if none fits the area is unserved.
Metrics: CONTRACT F-6, F-9 (p_i from JalPriority's result, default weights), F-10/F-11 (lambda 0.3, debt from history),
F-12, F-13, F-14 against the PREDICTED shortfall of the same PredictionSet.
"""
from __future__ import annotations

import pytest

from jp_a.data.debt import debt_by_area
from jp_a.domain.entities import PlanResult, Scenario

from .e2e_helpers import MAIN_ID, SEED_IDS, demo_areas, solve_body

LAMBDA = 0.3


def _route_km(scn: Scenario, stops: list[str]) -> float:
    path = [scn.depot.depot_id, *stops, scn.depot.depot_id]
    return round(sum(scn.distance_km[a][b] for a, b in zip(path, path[1:])), 2)


def baseline(scn: Scenario, order: list[str]) -> tuple[dict[str, int], float]:
    """Return (allocated litres per area, total travel km) for a service order."""
    by_id = {a.area_id: a for a in scn.areas}
    tankers = sorted((t for t in scn.tankers if t.status == "available"), key=lambda t: t.tanker_id)
    left_cap = {t.tanker_id: t.capacity_litres for t in tankers}
    stops: dict[str, list[str]] = {t.tanker_id: [] for t in tankers}
    alloc = {a.area_id: 0 for a in scn.areas}
    supply = scn.supply.tanker_water_available_litres
    for area_id in order:
        need = min((by_id[area_id].requested_litres // 100) * 100, supply)
        for t in tankers:
            if need <= 0:
                break
            if left_cap[t.tanker_id] <= 0:
                continue
            cur = stops[t.tanker_id]
            new_stops = cur if area_id in cur else [*cur, area_id]
            if _route_km(scn, new_stops) > t.max_route_km:
                continue
            give = min(need, left_cap[t.tanker_id])
            stops[t.tanker_id] = new_stops
            left_cap[t.tanker_id] -= give
            alloc[area_id] += give
            need -= give
            supply -= give
        if supply <= 0:
            break
    travel = round(sum(_route_km(scn, s) for s in stops.values() if s), 2)
    return alloc, travel


def fcfs_order(scn: Scenario) -> list[str]:
    return [a.area_id for a in sorted(scn.areas, key=lambda a: (a.request_time, a.area_id))]


def nearest_order(scn: Scenario) -> list[str]:
    d = scn.distance_km[scn.depot.depot_id]
    return sorted((a.area_id for a in scn.areas), key=lambda a: (d[a], a))


def metrics(alloc, travel, sf, p, debt) -> dict:
    u = {i: max(0, sf[i] - alloc[i]) for i in sf}
    cov = [round(min(1.0, alloc[i] / sf[i]), 4) for i in sf if sf[i] > 0]
    return {
        "priority_weighted_unmet": round(sum(p[i] * u[i] for i in sf), 1),
        "deprivation_debt_remaining_litres": sum(round((1 - LAMBDA) * debt.get(i, 0) + u[i]) for i in sf),
        "total_unmet_litres": sum(u.values()),
        "min_coverage_ratio": min(cov) if cov else 1.0,
        "total_travel_km": travel,
    }


@pytest.fixture(scope="module")
def runs(client_a):
    out = {}
    for sid in SEED_IDS:
        scn = client_a.scenario(sid)
        r = client_a.solve(solve_body(scn, time_limit_s=8.0))
        assert r.status_code == 200, r.text
        plan = PlanResult.model_validate(r.json())
        sf = {a.area_id: a.predicted_shortfall_litres for a in plan.allocations}
        p = {a.area_id: a.priority_weight for a in plan.allocations}
        debt = debt_by_area(scn)
        f_order, n_order = fcfs_order(scn), nearest_order(scn)
        f_alloc, f_km = baseline(scn, f_order)
        n_alloc, n_km = baseline(scn, n_order)
        out[sid] = {
            "scn": scn, "plan": plan, "sf": sf, "debt": debt,
            "f_order": f_order, "n_order": n_order, "f_alloc": f_alloc, "n_alloc": n_alloc,
            "fcfs": metrics(f_alloc, f_km, sf, p, debt),
            "nearest": metrics(n_alloc, n_km, sf, p, debt),
            "jp": {
                "priority_weighted_unmet": round(sum(a.priority_weight * a.unmet_litres for a in plan.allocations), 1),
                "min_coverage_ratio": min(
                    (a.coverage_ratio for a in plan.allocations if a.predicted_shortfall_litres > 0), default=1.0
                ),
                "total_travel_km": plan.totals.travel_km,
                "total_unmet_litres": plan.totals.unmet_litres,
            },
        }
    return out


def test_print_table(runs, capsys):
    with capsys.disabled():
        for sid, r in runs.items():
            print(f"\n{sid}")
            for k in ("jp", "fcfs", "nearest"):
                m = r[k]
                print(
                    f"  {k:8s} PWU={m['priority_weighted_unmet']:>9} unmet={m['total_unmet_litres']:>6} "
                    f"mincov={m['min_coverage_ratio']:.4f} km={m['total_travel_km']}"
                )
            print("  FCFS order   ", r["f_order"], {k: v for k, v in r["f_alloc"].items() if v})
            print("  nearest order", r["n_order"], {k: v for k, v in r["n_alloc"].items() if v})


@pytest.mark.parametrize("sid", SEED_IDS)
def test_1_service_orders_differ_in_at_least_3_positions(runs, sid):
    f, n = runs[sid]["f_order"], runs[sid]["n_order"]
    assert sum(a != b for a, b in zip(f, n)) >= 3


@pytest.mark.parametrize("sid", SEED_IDS)
def test_2_allocations_differ_for_at_least_2_areas(runs, sid):
    f, n = runs[sid]["f_alloc"], runs[sid]["n_alloc"]
    assert sum(f[a] != n[a] for a in f) >= 2


@pytest.mark.parametrize("sid", SEED_IDS)
def test_3_metrics_differ_in_at_least_2_of_pwu_mincov_travel(runs, sid):
    f, n = runs[sid]["fcfs"], runs[sid]["nearest"]
    diff = [k for k in ("priority_weighted_unmet", "min_coverage_ratio", "total_travel_km") if f[k] != n[k]]
    assert len(diff) >= 2, diff


@pytest.mark.parametrize("sid", SEED_IDS)
def test_4_nearest_first_travels_less_than_fcfs(runs, sid):
    assert runs[sid]["nearest"]["total_travel_km"] < runs[sid]["fcfs"]["total_travel_km"]


@pytest.mark.parametrize("sid", SEED_IDS)
def test_5_jalpriority_beats_both(runs, sid):
    r = runs[sid]
    for name in ("fcfs", "nearest"):
        assert r["jp"]["priority_weighted_unmet"] < r[name]["priority_weighted_unmet"], name
        assert r["jp"]["min_coverage_ratio"] >= r[name]["min_coverage_ratio"], name


def test_6_far_vulnerable_areas_are_starved_by_both_baselines(client_a, runs):
    r = runs[MAIN_ID]
    far = demo_areas(r["scn"], client_a.predictions(MAIN_ID))
    assert len(far) >= 2
    for area_id in far:
        for key in ("f_alloc", "n_alloc"):
            assert r[key][area_id] / r["sf"][area_id] < 0.10, (key, area_id)
    jp = {a.area_id: a.coverage_ratio for a in r["plan"].allocations}
    assert sum(jp[a] >= 0.3 for a in far) >= 2  # JalPriority actually serves them


def test_loud_mid_area_first_for_fcfs_and_quiet_near_area_earlier_for_nearest(runs):
    r = runs[MAIN_ID]
    scn = r["scn"]
    d = {a.area_id: scn.distance_km[scn.depot.depot_id][a.area_id] for a in scn.areas}
    by_id = {a.area_id: a for a in scn.areas}
    first = by_id[r["f_order"][0]]
    assert d[first.area_id] > sorted(d.values())[2]  # mid distance, not one of the 3 nearest
    assert first.requested_litres > r["sf"][first.area_id]  # loud
    quiet = [
        a for a in scn.areas if d[a.area_id] <= sorted(d.values())[2] and a.requested_litres < r["sf"][a.area_id]
    ]
    assert quiet
    assert all(r["n_order"].index(a.area_id) < r["f_order"].index(a.area_id) for a in quiet)
