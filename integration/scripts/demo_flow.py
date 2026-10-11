"""Scripted demo flow through the public base URL (JP_BASE_URL, default http://localhost:8080).

login -> scenario snapshot -> plan -> compare (JalPriority vs FCFS vs nearest-first) -> weights -> approve
-> replan tanker_down -> approve the revised plan. Prints the headline numbers; exits 1 on a failed check.
Run on a freshly reset demo (scripts/demo_reset) so the ledger starts clean.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.environ.get("JP_BASE_URL", "http://localhost:8080").rstrip("/")
SCN = "scn_sa01_w06"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/b/v1/auth/login", json={"username": "officer_01", "password": "jal-demo-2026"})
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["token"]

    snap = c.get(f"/b/v1/scenarios/{SCN}").json()
    pred = c.get(f"/b/v1/scenarios/{SCN}/predictions").json()
    supply = snap["supply"]["tanker_water_available_litres"]
    shortfall = sum(a["predicted_shortfall_litres"] for a in pred["areas"])
    print(f"snapshot: supply {supply} L, predicted shortfall {shortfall} L ({shortfall / supply:.2f}x supply), alert {snap['heat']['alert_level']}")

    c.put("/b/v1/weights", json={"service_area_id": "sa_01", "weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}})
    t0 = time.perf_counter()
    plan = c.post("/b/v1/plans", json={"scenario_id": SCN}).json()
    plan_s = time.perf_counter() - t0
    m = plan["metrics"]
    check("plan created, 0 violations", plan["result"]["constraint_check"]["violations"] == [] and m["constraint_violations"] == 0, f"{plan_s:.2f}s PWU={m['priority_weighted_unmet']}")

    cmp_ = c.post("/b/v1/comparisons", json={"scenario_id": SCN}).json()
    res = cmp_.get("metrics", {})
    if not res:
        print("comparison payload keys:", list(cmp_))
        failures.append("comparison shape")
    else:
        jp, fc, nf = (res[k]["priority_weighted_unmet"] for k in ("jalpriority", "fcfs", "nearest_first"))
        print(f"compare PWU: JalPriority {jp} | FCFS {fc} | nearest-first {nf}")
        for k in ("jalpriority", "fcfs", "nearest_first"):
            print(f"  {k:14s} unmet {res[k]['total_unmet_litres']:>6} L  min cov {res[k]['min_coverage_ratio']:.4f}  km {res[k]['total_travel_km']}")
        check("JalPriority beats both baselines (PWU)", jp < fc and jp < nf)
        check("JalPriority min coverage >= both", res["jalpriority"]["min_coverage_ratio"] >= max(res["fcfs"]["min_coverage_ratio"], res["nearest_first"]["min_coverage_ratio"]))

    dec = c.post(f"/b/v1/plans/{plan['plan_id']}/decision", json={"decision": "approved", "note": "demo flow"})
    check("approve plan", dec.status_code in (200, 201), str(dec.status_code))
    ledger = c.get("/b/v1/ledger", params={"scenario_id": SCN}).json()
    print("ledger keys:", list(ledger))

    tankers = [t["tanker_id"] for t in snap["tankers"] if t["status"] == "available"]
    t0 = time.perf_counter()
    rp = c.post("/b/v1/replans", json={"plan_id": plan["plan_id"], "disruption": {"type": "tanker_down", "params": {"tanker_id": tankers[0]}}})
    wall = time.perf_counter() - t0
    body = rp.json()
    new_plan = body.get("new_plan") or body.get("plan") or {}
    ms = (new_plan.get("metrics") or {}).get("replanning_time_ms")
    check("replan tanker_down < 5 s", rp.status_code == 201 and wall < 5 and (ms is None or ms < 5000), f"wall {wall:.2f}s replanning_time_ms={ms}")
    if new_plan:
        dec2 = c.post(f"/b/v1/plans/{new_plan['plan_id']}/decision", json={"decision": "approved", "note": "revised"})
        check("approve revised plan", dec2.status_code in (200, 201), str(dec2.status_code))
    print("OK" if not failures else f"FAILED: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
