"""Performance report for Member A: solve wall time, prediction latency and the disruption replan chain.

Runs the REAL app (committed ``data/`` and ``models/``, auth enabled, a minted dispatcher token) in-process through
``TestClient``. Derived and random scenarios are written to a temporary copy of ``data/`` so the repository is
never modified.

    python scripts/perf_report.py                 # prints the markdown tables and writes scripts/perf_report.md
    python scripts/perf_report.py --repeats 5 --out scripts/perf_report.md

Measured (wall clock around each HTTP call):

  (a) 3 seeds + 10 seeded random 10-area / 4-tanker scenarios:
        solve at time_limit_s = 8.0 and 3.0, prediction latency cold (first call) and cached (second call);
  (b) per seed and per disruption type (tanker_down on an available tanker, supply_cut 0.3, heat_alert +1 level)
      the replan chain  POST disruption -> GET predictions (cold, derived scenario) -> POST solve (time_limit_s 3.0).

Targets (MEMBER_A section 7): plan < 10 s with the default 8.0 s limit; replan chain < 4 s on A's side.
Exit code 1 when a target is missed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jp_a.config import Settings  # noqa: E402
from jp_a.data.debt import debt_by_area  # noqa: E402
from jp_a.domain.entities import Scenario  # noqa: E402
from jp_a.main import create_app  # noqa: E402

SEED_IDS = ("scn_sa01_w06", "scn_sa01_w06_severe", "scn_sa01_w06_mild")
N_RANDOM = 10
RANDOM_AREAS = 10
RANDOM_TANKERS = 4
WARMUP_ID = "scn_perf_warmup"
SOLVE_LIMITS = (8.0, 3.0)
CHAIN_LIMIT_S = 3.0
PLAN_TARGET_S = 10.0
CHAIN_TARGET_S = 4.0
WEIGHTS = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
DISRUPTIONS = ("tanker_down", "supply_cut", "heat_alert")
DEV_SECRET = "dev-only-jalpriority-secret-change-me-0123456789"


# --------------------------------------------------------------------------- data
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def random_scenario(index: int, base: dict) -> Scenario:
    """Seeded 10-area / 4-tanker scenario cloned from the main seed: real histories (rescaled), fresh geometry."""
    rng = random.Random(7000 + index)
    seed = json.loads(json.dumps(base))
    depot = seed["depot"]
    km_lat = 111.0
    km_lon = 111.0 * math.cos(math.radians(depot["lat"]))
    areas = []
    for n in range(1, RANDOM_AREAS + 1):
        src = json.loads(json.dumps(base["areas"][(n - 1) % len(base["areas"])]))
        radius = rng.uniform(1.5, 12.0)
        angle = rng.uniform(0.0, 2 * math.pi)
        scale = rng.uniform(0.7, 1.4)
        for h in src["history"]:
            h["need_litres"] = int(round(h["need_litres"] * scale))
            h["received_litres"] = int(round(h["received_litres"] * scale * rng.uniform(0.75, 1.0)))
        src.update(
            area_id=f"ar_{n:02d}",
            name=f"Ward {n:02d} (synthetic)",
            lat=round(depot["lat"] + radius * math.sin(angle) / km_lat, 4),
            lon=round(depot["lon"] + radius * math.cos(angle) / km_lon, 4),
            population=int(src["population"] * scale),
            vulnerability_index=round(rng.uniform(0.1, 0.95), 2),
            heat_exposure=round(rng.uniform(0.2, 0.95), 2),
            expected_piped_litres=int(src["expected_piped_litres"] * scale * rng.uniform(0.8, 1.2)),
            requested_litres=rng.randrange(3000, 9100, 100),
        )
        areas.append(src)
    tankers = [
        {
            "tanker_id": f"tk_{n:02d}",
            "capacity_litres": rng.randrange(9000, 12100, 100),
            "status": "available",
            "max_route_km": float(rng.choice([40.0, 60.0])),
        }
        for n in range(1, RANDOM_TANKERS + 1)
    ]
    capacity = sum(t["capacity_litres"] for t in tankers)
    level = rng.randint(1, 3)
    nodes = {depot["depot_id"]: (depot["lat"], depot["lon"])}
    nodes.update({a["area_id"]: (a["lat"], a["lon"]) for a in areas})
    matrix = {
        a: {b: 0.0 if a == b else round(_haversine_km(*nodes[a], *nodes[b]) * 1.3, 2) for b in nodes} for a in nodes
    }
    seed.update(
        scenario_id=f"scn_perf_r{index:02d}",
        areas=areas,
        tankers=tankers,
        supply={"tanker_water_available_litres": int(capacity * rng.uniform(0.45, 0.9)) // 100 * 100},
        heat={
            "alert_level": level,
            "max_temp_c": round(base["heat"]["max_temp_c"] + 1.5 * (level - base["heat"]["alert_level"]), 1),
            "heat_index_c": round(base["heat"]["heat_index_c"] + 2.5 * (level - base["heat"]["alert_level"]), 1),
        },
        distance_km=matrix,
        seed=base["seed"] + index,
    )
    return Scenario.model_validate(seed)


def _write(data_dir: Path, scenario: Scenario) -> None:
    path = data_dir / "scenarios" / f"{scenario.scenario_id}.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(scenario.model_dump(mode="json"), fh, indent=2)
        fh.write("\n")


def build_data_dir(dst: Path, n_random: int) -> list[str]:
    """Copy the committed data to ``dst`` and add the random + warm-up scenarios. Returns the random ids."""
    for sub in ("scenarios", "truth"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
        for src in sorted((ROOT / "data" / sub).glob("*.json")):
            shutil.copyfile(src, dst / sub / src.name)
    labels = ROOT / "data" / "labels.json"
    if labels.is_file():
        shutil.copyfile(labels, dst / "labels.json")
    (dst / "derived").mkdir(exist_ok=True)
    base = json.loads((dst / "scenarios" / f"{SEED_IDS[0]}.json").read_text(encoding="utf-8"))
    warm = json.loads(json.dumps(base))
    warm["scenario_id"] = WARMUP_ID
    _write(dst, Scenario.model_validate(warm))
    ids = []
    for i in range(1, n_random + 1):
        scenario = random_scenario(i, base)
        _write(dst, scenario)
        ids.append(scenario.scenario_id)
    return ids


# --------------------------------------------------------------------------- app
def _token(settings: Settings) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.shared_jwt_issuer,
        "aud": settings.shared_jwt_audience,
        "sub": "officer_01",
        "role": "dispatcher",
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(claims, settings.shared_jwt_secret, algorithm="HS256")


class Api:
    """The real app behind a TestClient; every call carries a real dispatcher token (auth ENABLED)."""

    def __init__(self, http: TestClient, settings: Settings):
        self.http = http
        self.headers = {"Authorization": f"Bearer {_token(settings)}"}

    def get(self, path: str):
        return self.http.get(f"/a/v1{path}", headers=self.headers)

    def post(self, path: str, body: dict):
        return self.http.post(f"/a/v1{path}", json=body, headers=self.headers)

    def scenario(self, scenario_id: str) -> Scenario:
        r = self.get(f"/scenarios/{scenario_id}")
        r.raise_for_status()
        return Scenario.model_validate(r.json())

    def solve_body(self, scenario: Scenario, time_limit_s: float) -> dict:
        return {
            "scenario_id": scenario.scenario_id,
            "weights": dict(WEIGHTS),
            "debt_by_area": debt_by_area(scenario),
            "time_limit_s": time_limit_s,
        }


@contextmanager
def world(n_random: int) -> Iterator[tuple[Api, list[str]]]:
    """A fresh real app on a fresh temporary copy of the data (model loaded at startup, predictor warmed up)."""
    with tempfile.TemporaryDirectory(prefix="jp_perf_") as tmp:
        data_dir = Path(tmp)
        random_ids = build_data_dir(data_dir, n_random)
        settings = Settings(
            a_data_dir=data_dir,
            a_models_dir=ROOT / "models",
            a_auth_disabled=False,
            shared_jwt_secret=DEV_SECRET,
            shared_jwt_issuer="jalpriority-b",
            shared_jwt_audience="jalpriority",
            shared_env="local",
            shared_log_level="WARNING",
        )
        with TestClient(create_app(settings)) as http:
            api = Api(http, settings)
            # Process-level warm-up (XGBoost / OR-Tools imports, first JIT-ish calls) on a throw-away scenario so
            # the first measured "cold" prediction is a cache miss, not an import.
            assert api.get(f"/scenarios/{WARMUP_ID}/predictions").status_code == 200
            warm = api.scenario(WARMUP_ID)
            assert api.post("/plans/solve", api.solve_body(warm, 1.0)).status_code == 200
            yield api, random_ids


# --------------------------------------------------------------------------- measurements
def timed(fn: Callable[[], object]) -> tuple[float, object]:
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out


def measure_scenario(api: Api, scenario_id: str, repeats: int) -> dict:
    cold_s, r = timed(lambda: api.get(f"/scenarios/{scenario_id}/predictions"))
    assert r.status_code == 200, r.text
    cold_api_ms = r.json()["latency_ms"]
    cached_s, r2 = timed(lambda: api.get(f"/scenarios/{scenario_id}/predictions"))
    assert r2.status_code == 200, r2.text
    scenario = api.scenario(scenario_id)
    out: dict = {
        "scenario_id": scenario_id,
        "n_areas": len(scenario.areas),
        "n_tankers": len(scenario.tankers),
        "pred_cold_s": cold_s,
        "pred_cold_api_ms": cold_api_ms,
        "pred_cached_s": cached_s,
    }
    for limit in SOLVE_LIMITS:
        walls, statuses = [], []
        for _ in range(repeats):
            wall, resp = timed(lambda: api.post("/plans/solve", api.solve_body(scenario, limit)))
            assert resp.status_code == 200, resp.text
            plan = resp.json()
            assert not plan["constraint_check"]["violations"], f"{scenario_id}: F-15 violations"
            walls.append(wall)
            statuses.append(f"{plan['solver']['engine']}/{plan['solver']['status']}")
        out[f"solve_{limit}"] = walls
        out[f"status_{limit}"] = statuses
    return out


def disruption_params(scenario: Scenario, kind: str) -> dict:
    if kind == "tanker_down":
        tanker = next(t for t in scenario.tankers if t.status == "available")
        return {"tanker_id": tanker.tanker_id}
    if kind == "supply_cut":
        return {"fraction": 0.3}
    level = scenario.heat.alert_level
    return {"alert_level": level + 1 if level < 3 else level - 1}  # +1; the seed already at the maximum (3) goes to 2


def measure_chain(seed_id: str, kind: str, repeats: int) -> dict:
    """Disruption + predict + solve on a FRESH app / data dir per repeat, so every repeat is a cold chain."""
    rows = []
    params: dict = {}
    for _ in range(repeats):
        with world(0) as (api, _):
            parent = api.scenario(seed_id)
            params = disruption_params(parent, kind)
            body_debt = debt_by_area(parent)  # client-side input (B's ledger); computed before the clock starts
            t0 = time.perf_counter()
            r = api.post(f"/scenarios/{seed_id}/disruptions", {"type": kind, "params": params})
            t1 = time.perf_counter()
            assert r.status_code == 201, r.text
            derived_id = r.json()["scenario_id"]
            p = api.get(f"/scenarios/{derived_id}/predictions")
            t2 = time.perf_counter()
            assert p.status_code == 200, p.text
            s = api.post(
                "/plans/solve",
                {"scenario_id": derived_id, "weights": dict(WEIGHTS), "debt_by_area": body_debt, "time_limit_s": CHAIN_LIMIT_S},
            )
            t3 = time.perf_counter()
            assert s.status_code == 200, s.text
            plan = s.json()
            assert not plan["constraint_check"]["violations"]
            rows.append(
                {
                    "total": t3 - t0,
                    "disrupt": t1 - t0,
                    "predict": t2 - t1,
                    "solve": t3 - t2,
                    "status": f"{plan['solver']['engine']}/{plan['solver']['status']}",
                }
            )
    return {"seed": seed_id, "kind": kind, "params": params, "rows": rows}


# --------------------------------------------------------------------------- formatting
def stats(values: list[float]) -> tuple[float, float, float]:
    return min(values), statistics.median(values), max(values)


def fmt_s(v: float) -> str:
    return f"{v:.3f}"


def fmt_ms(v: float) -> str:
    return f"{v * 1000:.1f}"


def row(label: str, values: list[float], fmt: Callable[[float], str] = fmt_s) -> str:
    lo, md, hi = stats(values)
    return f"| {label} | {len(values)} | {fmt(lo)} | {fmt(md)} | {fmt(hi)} |"


def render(per_scenario: list[dict], chains: list[dict], repeats: int) -> tuple[str, bool]:
    seeds = [m for m in per_scenario if m["scenario_id"] in SEED_IDS]
    rand = [m for m in per_scenario if m["scenario_id"] not in SEED_IDS]
    lines: list[str] = []
    add = lines.append
    add("# JalPriority Member A - performance report")
    add("")
    add(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')} by `scripts/perf_report.py` "
        f"({repeats} repeats per measurement). Host: {platform.platform()}, {os.cpu_count()} logical CPUs, "
        f"Python {platform.python_version()}. Real app in-process (`TestClient`), auth enabled with a minted dispatcher "
        "token, committed model and data (copied to a temp dir). All times are wall clock around the HTTP call."
    )
    add("")
    add("## (a) Solve wall time, seconds (POST /plans/solve, default weights, debt from history)")
    add("")
    add("| Group / limit | samples | min | median | max |")
    add("|---|---|---|---|---|")
    for limit in SOLVE_LIMITS:
        key = f"solve_{limit}"
        for name, group in (("3 seeds", seeds), (f"{len(rand)} random 10-area/4-tanker", rand), ("all", per_scenario)):
            samples = [w for m in group for w in m[key]]
            if samples:
                add(row(f"{name} @ time_limit_s={limit}", samples))
    add("")
    add("### Solve wall time per scenario (median of repeats, seconds; engine/status)")
    add("")
    add("| scenario | areas | tankers | @8.0 s median | @8.0 s max | @3.0 s median | @3.0 s max | status @8.0 | status @3.0 |")
    add("|---|---|---|---|---|---|---|---|---|")
    for m in per_scenario:
        s8, s3 = m["solve_8.0"], m["solve_3.0"]
        add(
            f"| {m['scenario_id']} | {m['n_areas']} | {m['n_tankers']} | {fmt_s(statistics.median(s8))} | {fmt_s(max(s8))} "
            f"| {fmt_s(statistics.median(s3))} | {fmt_s(max(s3))} | {', '.join(sorted(set(m['status_8.0'])))} "
            f"| {', '.join(sorted(set(m['status_3.0'])))} |"
        )
    add("")
    add("## (a) Prediction latency, milliseconds (GET /scenarios/{id}/predictions)")
    add("")
    add("| Measure | samples | min | median | max |")
    add("|---|---|---|---|---|")
    for name, group in (("3 seeds", seeds), (f"{len(rand)} random", rand), ("all", per_scenario)):
        if group:
            add(row(f"cold, wall, {name}", [m["pred_cold_s"] for m in group], fmt_ms))
            add(row(f"cold, API `latency_ms`, {name}", [m["pred_cold_api_ms"] / 1000 for m in group], fmt_ms))
            add(row(f"cached, wall, {name}", [m["pred_cached_s"] for m in group], fmt_ms))
    add("")
    add(f"## (b) Replan chain: disruption + predict (cold) + solve @ time_limit_s={CHAIN_LIMIT_S}, seconds")
    add("")
    add("| seed | disruption | params | samples | min | median | max | disrupt (med) | predict (med) | solve (med) | status |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for c in chains:
        tot = [r["total"] for r in c["rows"]]
        lo, md, hi = stats(tot)
        status = ", ".join(sorted({r["status"] for r in c["rows"]}))
        add(
            f"| {c['seed']} | {c['kind']} | `{json.dumps(c['params'], sort_keys=True)}` | {len(tot)} | {fmt_s(lo)} | {fmt_s(md)} "
            f"| {fmt_s(hi)} | {fmt_s(statistics.median(r['disrupt'] for r in c['rows']))} "
            f"| {fmt_s(statistics.median(r['predict'] for r in c['rows']))} "
            f"| {fmt_s(statistics.median(r['solve'] for r in c['rows']))} | {status} |"
        )
    add("")
    add("| chain aggregate | samples | min | median | max |")
    add("|---|---|---|---|---|")
    for kind in DISRUPTIONS:
        add(row(f"{kind}", [r["total"] for c in chains if c["kind"] == kind for r in c["rows"]]))
    add(row("all disruption types", [r["total"] for c in chains for r in c["rows"]]))
    add("")
    add("`heat_alert` is +1 level (2 -> 3) on the main and mild seeds; the severe seed is already at the maximum "
        "level 3, so it uses 3 -> 2.")
    add("")
    plan_max = max(w for m in per_scenario for w in m["solve_8.0"])
    chain_max = max(r["total"] for c in chains for r in c["rows"])
    ok_plan, ok_chain = plan_max < PLAN_TARGET_S, chain_max < CHAIN_TARGET_S
    add("## Targets")
    add("")
    add(f"- {'PASS' if ok_plan else 'FAIL'}: plan wall time at time_limit_s=8.0, max {plan_max:.3f} s (target < {PLAN_TARGET_S:g} s)")
    add(f"- {'PASS' if ok_chain else 'FAIL'}: replan chain at time_limit_s=3.0, max {chain_max:.3f} s (target < {CHAIN_TARGET_S:g} s)")
    add("")
    return "\n".join(lines), ok_plan and ok_chain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=3, help="samples per solve / chain measurement (default 3)")
    parser.add_argument("--random", type=int, default=N_RANDOM, help="number of random scenarios (default 10)")
    parser.add_argument("--out", default=str(ROOT / "scripts" / "perf_report.md"))
    args = parser.parse_args(argv)

    per_scenario: list[dict] = []
    with world(args.random) as (api, random_ids):
        for scenario_id in [*SEED_IDS, *random_ids]:
            per_scenario.append(measure_scenario(api, scenario_id, args.repeats))
            print(f"measured {scenario_id}", file=sys.stderr)
    chains = []
    for seed_id in SEED_IDS:
        for kind in DISRUPTIONS:
            chains.append(measure_chain(seed_id, kind, args.repeats))
            print(f"chain {seed_id} {kind}", file=sys.stderr)
    text, ok = render(per_scenario, chains, args.repeats)
    sys.stdout.write(text + "\n")
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
