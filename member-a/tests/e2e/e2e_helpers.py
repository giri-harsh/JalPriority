"""Helpers for the end-to-end tests: an isolated copy of the real data, a wide scenario, FCFS / nearest-first
baselines and the demo-property recomputation.

Everything here works from API responses (strict models) and the committed ``data/`` and ``models/``; nothing
is written to the repository because derived scenarios land in a temporary ``A_DATA_DIR``.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from jp_a.data.debt import debt_by_area
from jp_a.domain.entities import PredictionSet, Scenario

MEMBER_A_DIR = Path(__file__).resolve().parents[2]
REAL_DATA_DIR = MEMBER_A_DIR / "data"
REAL_MODELS_DIR = MEMBER_A_DIR / "models"

MAIN_ID = "scn_sa01_w06"
SEVERE_ID = "scn_sa01_w06_severe"
MILD_ID = "scn_sa01_w06_mild"
SEED_IDS = (MAIN_ID, SEVERE_ID, MILD_ID)
WIDE_ID = "scn_sa01_w07"  # 10-area scenario written into the temporary data dir
WIDE_AREAS = 10

DEFAULT_WEIGHTS = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}

# demo-property thresholds (MEMBER_A F1)
MIN_VULNERABILITY = 0.6
RANK_K = 3
LOW_VULNERABILITY = 0.4
MIN_DEMO_AREAS = 2
MIN_SHORTFALL_TO_SUPPLY = 1.6


# --------------------------------------------------------------------------- data isolation
def copy_real_data(dst: Path) -> Path:
    """Copy committed seeds, truth and labels to ``dst`` so tests can create derived scenarios freely."""
    for sub in ("scenarios", "truth"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
        for src in sorted((REAL_DATA_DIR / sub).glob("*.json")):
            (dst / sub / src.name).write_bytes(src.read_bytes())
    labels = REAL_DATA_DIR / "labels.json"
    if labels.is_file():
        (dst / "labels.json").write_bytes(labels.read_bytes())
    (dst / "derived").mkdir(exist_ok=True)
    return dst


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def write_wide_scenario(data_dir: Path, scenario_id: str = WIDE_ID, n_areas: int = WIDE_AREAS) -> Scenario:
    """A 10-area scenario cloned from the main seed (real histories, jittered coordinates, valid matrix)."""
    seed = json.loads((data_dir / "scenarios" / f"{MAIN_ID}.json").read_text(encoding="utf-8"))
    areas = seed["areas"]
    out = []
    for n in range(1, n_areas + 1):
        src = json.loads(json.dumps(areas[(n - 1) % len(areas)]))
        src["area_id"] = f"ar_{n:02d}"
        src["name"] = f"Ward {n:02d} (synthetic)"
        if n > len(areas):
            src["lat"] = round(src["lat"] + 0.004 * (n - len(areas)), 4)
            src["lon"] = round(src["lon"] - 0.003 * (n - len(areas)), 4)
            src["population"] = int(src["population"] * 0.9)
        out.append(src)
    nodes = {seed["depot"]["depot_id"]: (seed["depot"]["lat"], seed["depot"]["lon"])}
    nodes.update({a["area_id"]: (a["lat"], a["lon"]) for a in out})
    matrix = {
        a: {b: 0.0 if a == b else round(_haversine_km(*nodes[a], *nodes[b]) * 1.3, 2) for b in nodes} for a in nodes
    }
    seed.update(scenario_id=scenario_id, areas=out, distance_km=matrix)
    scenario = Scenario.model_validate(seed)
    with open(data_dir / "scenarios" / f"{scenario_id}.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(scenario.model_dump(mode="json"), fh, indent=2)
    return scenario


# --------------------------------------------------------------------------- derived quantities
def solve_body(scenario: Scenario, weights: dict[str, float] | None = None, time_limit_s: float | None = None) -> dict:
    """POST /plans/solve body: default weights and the F-10 debt (lambda = 0.3) derived from the history."""
    body: dict[str, Any] = {
        "scenario_id": scenario.scenario_id,
        "weights": dict(weights or DEFAULT_WEIGHTS),
        "debt_by_area": debt_by_area(scenario),
    }
    if time_limit_s is not None:
        body["time_limit_s"] = time_limit_s
    return body


def shortfalls(prediction: PredictionSet) -> dict[str, int]:
    return {p.area_id: p.predicted_shortfall_litres for p in prediction.areas}


def depot_km(scenario: Scenario) -> dict[str, float]:
    return {a.area_id: scenario.distance_km[scenario.depot.depot_id][a.area_id] for a in scenario.areas}


def _top(keys: Iterable[str], rank_key, k: int) -> set[str]:
    return set(sorted(keys, key=rank_key)[:k])


def far_set(scenario: Scenario, k: int = RANK_K) -> set[str]:
    d = depot_km(scenario)
    return _top(d, lambda a: (-d[a], a), k)


def near_set(scenario: Scenario, k: int = RANK_K) -> set[str]:
    d = depot_km(scenario)
    return _top(d, lambda a: (d[a], a), k)


def late_requests(scenario: Scenario, k: int = RANK_K) -> set[str]:
    ordered = sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id))
    return {a.area_id for a in ordered[-k:]}


def early_requests(scenario: Scenario, k: int = RANK_K) -> set[str]:
    ordered = sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id))
    return {a.area_id for a in ordered[:k]}


def demo_areas(scenario: Scenario, prediction: PredictionSet) -> list[str]:
    """Areas satisfying every F1 condition on the PREDICTED shortfall the API serves."""
    sf = shortfalls(prediction)
    debt = debt_by_area(scenario)
    far, late = far_set(scenario), late_requests(scenario)
    return sorted(
        a.area_id
        for a in scenario.areas
        if a.vulnerability_index >= MIN_VULNERABILITY
        and a.area_id in far
        and a.area_id in late
        and a.requested_litres < sf[a.area_id]
        and debt[a.area_id] > 0
    )


def over_requesting_areas(scenario: Scenario, prediction: PredictionSet) -> list[str]:
    """Near, early, low-vulnerability areas that request more than their predicted shortfall."""
    sf = shortfalls(prediction)
    near, early = near_set(scenario), early_requests(scenario)
    return sorted(
        a.area_id
        for a in scenario.areas
        if a.area_id in near
        and a.area_id in early
        and a.vulnerability_index < LOW_VULNERABILITY
        and a.requested_litres > sf[a.area_id]
    )


# --------------------------------------------------------------------------- baselines (requested litres)
def _serve_in_order(order: list[str], requested: dict[str, int], supply: int) -> dict[str, int]:
    """Hand out ``requested`` litres in ``order`` until the supply is gone (tanker routing ignored)."""
    left = supply
    out = {area_id: 0 for area_id in order}
    for area_id in order:
        give = min(requested[area_id], left)
        out[area_id] = give
        left -= give
    return out


def fcfs_allocation(scenario: Scenario) -> dict[str, int]:
    """First come, first served: by ``request_time`` (ties by area id), serve the requested litres."""
    order = [a.area_id for a in sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id))]
    requested = {a.area_id: a.requested_litres for a in scenario.areas}
    return _serve_in_order(order, requested, scenario.supply.tanker_water_available_litres)


def nearest_first_allocation(scenario: Scenario) -> dict[str, int]:
    """Nearest first: by road distance from the depot (ties by area id), serve the requested litres."""
    d = depot_km(scenario)
    order = sorted(d, key=lambda a: (d[a], a))
    requested = {a.area_id: a.requested_litres for a in scenario.areas}
    return _serve_in_order(order, requested, scenario.supply.tanker_water_available_litres)


def coverage(allocated: dict[str, int], shortfall: dict[str, int]) -> dict[str, float]:
    """F-7 on litres actually needed: water beyond the shortfall is wasted and does not raise coverage."""
    return {
        a: 1.0 if shortfall[a] == 0 else min(1.0, allocated.get(a, 0) / shortfall[a]) for a in shortfall
    }


def wasted_litres(allocated: dict[str, int], shortfall: dict[str, int]) -> int:
    return sum(max(0, allocated.get(a, 0) - shortfall[a]) for a in shortfall)


# --------------------------------------------------------------------------- disruption maths
def expected_supply_after_cut(supply: int, fraction: float) -> int:
    """CONTRACT 5.12 ``floor(S * (1 - f) / 100) * 100`` evaluated exactly (see CONTRACT_ISSUES CI-3)."""
    exact = Fraction(supply) * (1 - Fraction(str(fraction))) / 100
    return max(0, math.floor(exact) * 100)


# --------------------------------------------------------------------------- API wrapper
class Client:
    """Thin typed wrapper over the TestClient: every call carries the dispatcher token."""

    def __init__(self, http: TestClient, headers: dict[str, str]):
        self.http, self.headers = http, headers

    def scenario(self, scenario_id: str) -> Scenario:
        r = self.http.get(f"/a/v1/scenarios/{scenario_id}", headers=self.headers)
        assert r.status_code == 200, r.text
        return Scenario.model_validate(r.json())

    def predictions(self, scenario_id: str) -> PredictionSet:
        r = self.http.get(f"/a/v1/scenarios/{scenario_id}/predictions", headers=self.headers)
        assert r.status_code == 200, r.text
        return PredictionSet.model_validate(r.json())

    def solve(self, body: dict):
        return self.http.post("/a/v1/plans/solve", json=body, headers=self.headers)

    def disrupt(self, scenario_id: str, dtype: str, params: dict):
        return self.http.post(
            f"/a/v1/scenarios/{scenario_id}/disruptions", json={"type": dtype, "params": params}, headers=self.headers
        )
