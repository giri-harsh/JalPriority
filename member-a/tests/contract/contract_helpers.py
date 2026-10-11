"""Helpers for the live contract tests (test_contract_*.py).

Everything here is written from CONTRACT.md alone and deliberately does NOT import production formula code:

* ``*_KEYS`` constants: the exact key sets of every section 5 / 6 entity, copied from the contract.
* ``assert_*_keys``: recursive exact key-set checks on raw JSON dicts.
* ``assert_iso_datetimes``: every datetime-looking string ends in ``Z`` with milliseconds (section 4.3).
* ``expected_plan``: an independent re-implementation of F-1..F-8 (and the F-15 count) over a Scenario dict and a
  PredictionSet dict.
* ``real_settings`` / ``copy_real_data``: build an isolated ``A_DATA_DIR`` so derived scenarios never land in ``data/``.
* ``error_envelope_ok``: the section 4.5 envelope check.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

MEMBER_A_DIR = Path(__file__).resolve().parents[2]
REAL_DATA_DIR = MEMBER_A_DIR / "data"
REAL_MODELS_DIR = MEMBER_A_DIR / "models"

API = "/a/v1"
SEEDS = ("scn_sa01_w06", "scn_sa01_w06_severe", "scn_sa01_w06_mild")

# --------------------------------------------------------------------------- section 4.2 id patterns
SERVICE_AREA_RE = re.compile(r"^sa_\d{2}$")
AREA_RE = re.compile(r"^ar_\d{2}$")
TANKER_RE = re.compile(r"^tk_\d{2}$")
DEPOT_RE = re.compile(r"^dp_\d{2}$")
SEED_SCENARIO_RE = re.compile(r"^scn_[a-z0-9_]+$")
DERIVED_SCENARIO_RE = re.compile(r"^scn_[a-z0-9_]+__d\d+_[0-9a-f]{6}$")
RESULT_RE = re.compile(r"^res_[0-9a-f]{12}$")
REQUEST_RE = re.compile(r"^req_[0-9a-f]{12}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
ISO_LOOSE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def is_scenario_id(value: str) -> bool:
    if "__" in value:
        return bool(DERIVED_SCENARIO_RE.match(value)) and "__" not in value.split("__", 1)[0]
    return bool(SEED_SCENARIO_RE.match(value))


# --------------------------------------------------------------------------- exact key sets (CONTRACT section 5 / 6)
HEALTH_KEYS = {"status", "service", "version", "model_version", "time"}
SCENARIO_LIST_KEYS = {"scenarios"}
SCENARIO_SUMMARY_KEYS = {
    "schema_version", "scenario_id", "parent_scenario_id", "service_area_id", "window_index", "label",
    "n_areas", "supply_litres", "tankers_available", "alert_level", "is_synthetic",
}  # fmt: skip
SCENARIO_KEYS = {
    "schema_version", "scenario_id", "parent_scenario_id", "service_area_id", "service_area_name", "window_index",
    "window_start", "window_hours", "is_synthetic", "seed", "heat", "supply", "depot", "tankers", "areas",
    "distance_km", "disruptions_applied",
}  # fmt: skip
HEAT_KEYS = {"alert_level", "max_temp_c", "heat_index_c"}
SUPPLY_KEYS = {"tanker_water_available_litres"}
DEPOT_KEYS = {"depot_id", "name", "lat", "lon"}
TANKER_KEYS = {"tanker_id", "capacity_litres", "status", "max_route_km"}
AREA_KEYS = {
    "area_id", "name", "lat", "lon", "population", "vulnerability_index", "vulnerability_available",
    "heat_exposure", "expected_piped_litres", "requested_litres", "request_time", "last_delivery_litres",
    "last_delivery_at", "history",
}  # fmt: skip
HISTORY_KEYS = {"window_index", "need_litres", "received_litres", "max_temp_c", "alert_level"}
APPLIED_KEYS = {"type", "params", "applied_at"}
PREDICTION_KEYS = {
    "schema_version", "scenario_id", "model_version", "generated_at", "latency_ms", "run_mae_litres",
    "is_simulation", "areas",
}  # fmt: skip
AREA_PREDICTION_KEYS = {
    "area_id", "predicted_need_litres", "expected_piped_litres", "predicted_shortfall_litres",
    "baseline_last_need_litres", "top_features",
}  # fmt: skip
TOP_FEATURE_KEYS = {"feature", "contribution_litres"}
ALLOWED_FEATURES = {
    "max_temp_c", "heat_index_c", "alert_level", "heat_exposure", "population", "last_need_litres",
    "mean_need_last3_litres", "last_received_litres", "expected_piped_litres",
}  # fmt: skip
MODEL_INFO_KEYS = {
    "schema_version", "model_version", "trained_at", "train_rows", "test_rows", "target", "metrics",
    "feature_importances", "is_simulation", "disclaimer",
}  # fmt: skip
MODEL_METRICS_KEYS = {"xgboost", "last_observed_baseline"}
ERROR_METRIC_KEYS = {"mae_litres", "rmse_litres"}
FEATURE_IMPORTANCE_KEYS = {"feature", "importance"}
PLAN_KEYS = {
    "schema_version", "result_id", "strategy", "scenario_id", "service_area_id", "window_index", "generated_at",
    "weights", "solver", "prediction", "totals", "allocations", "routes", "unserved", "constraint_check",
}  # fmt: skip
WEIGHT_KEYS = {"urgency", "debt", "vulnerability", "travel"}
SOLVER_KEYS = {"engine", "status", "solve_time_ms", "time_limit_s"}
PREDICTION_REF_KEYS = {"model_version", "latency_ms", "run_mae_litres"}
TOTALS_KEYS = {
    "supply_available_litres", "tanker_capacity_available_litres", "shortfall_litres", "allocated_litres",
    "unmet_litres", "travel_km",
}  # fmt: skip
ALLOCATION_KEYS = {
    "area_id", "rank", "predicted_shortfall_litres", "allocated_litres", "unmet_litres", "coverage_ratio",
    "priority_weight", "urgency", "debt_litres_used", "debt_normalized", "vulnerability_used", "deliveries",
    "reasons", "reason_text", "is_unserved_remainder",
}  # fmt: skip
DELIVERY_KEYS = {"tanker_id", "stop_order", "litres"}
REASON_KEYS = {"code", "params"}
ROUTE_KEYS = {
    "tanker_id", "status", "capacity_litres", "max_route_km", "load_litres", "route_km", "stops", "path",
}  # fmt: skip
STOP_KEYS = {"stop_order", "area_id", "litres"}
UNSERVED_KEYS = {"area_id", "unmet_litres", "reason_code"}
CONSTRAINT_KEYS = {"water_cap_ok", "capacity_ok", "route_limit_ok", "unavailable_tanker_ok", "violations"}
ERROR_ENVELOPE_KEYS = {"error"}
ERROR_BODY_KEYS = {"code", "message", "details", "request_id"}

REASON_CODES = {
    "HIGH_PREDICTED_SHORTFALL", "HIGH_DEBT", "HIGH_VULNERABILITY", "HEAT_ALERT", "LOWER_RELATIVE_PRIORITY",
    "FULLY_SERVED", "PARTIALLY_SERVED", "NOT_SERVED", "NO_SHORTFALL", "SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT",
    "ROUTE_LIMIT", "TRAVEL_COST_TRADEOFF", "TIME_LIMITED_SOLUTION", "FCFS_ORDER", "NEAREST_FIRST_ORDER",
}  # fmt: skip
UNSERVED_REASON_CODES = {
    "SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT", "TRAVEL_COST_TRADEOFF", "LOWER_RELATIVE_PRIORITY",
}  # fmt: skip


def assert_keys(obj: Any, expected: set[str], where: str) -> None:
    """``obj`` is a dict whose key set equals ``expected`` exactly (no missing, no extra keys)."""
    assert isinstance(obj, dict), f"{where}: expected an object, got {type(obj).__name__}"
    keys = set(obj)
    assert keys == expected, f"{where}: missing={sorted(expected - keys)} extra={sorted(keys - expected)}"


def assert_scenario_keys(d: dict) -> None:
    assert_keys(d, SCENARIO_KEYS, "scenario")
    assert_keys(d["heat"], HEAT_KEYS, "scenario.heat")
    assert_keys(d["supply"], SUPPLY_KEYS, "scenario.supply")
    assert_keys(d["depot"], DEPOT_KEYS, "scenario.depot")
    for t in d["tankers"]:
        assert_keys(t, TANKER_KEYS, f"tanker {t.get('tanker_id')}")
    for a in d["areas"]:
        assert_keys(a, AREA_KEYS, f"area {a.get('area_id')}")
        for h in a["history"]:
            assert_keys(h, HISTORY_KEYS, f"history {a.get('area_id')}")
    for ap in d["disruptions_applied"]:
        assert_keys(ap, APPLIED_KEYS, "disruptions_applied[]")


def assert_prediction_keys(d: dict) -> None:
    assert_keys(d, PREDICTION_KEYS, "prediction set")
    for a in d["areas"]:
        assert_keys(a, AREA_PREDICTION_KEYS, f"area prediction {a.get('area_id')}")
        for f in a["top_features"]:
            assert_keys(f, TOP_FEATURE_KEYS, "top_features[]")


def assert_model_info_keys(d: dict) -> None:
    assert_keys(d, MODEL_INFO_KEYS, "model info")
    assert_keys(d["metrics"], MODEL_METRICS_KEYS, "model info.metrics")
    assert_keys(d["metrics"]["xgboost"], ERROR_METRIC_KEYS, "metrics.xgboost")
    assert_keys(d["metrics"]["last_observed_baseline"], ERROR_METRIC_KEYS, "metrics.last_observed_baseline")
    for f in d["feature_importances"]:
        assert_keys(f, FEATURE_IMPORTANCE_KEYS, "feature_importances[]")


def assert_plan_keys(d: dict) -> None:
    assert_keys(d, PLAN_KEYS, "plan result")
    assert_keys(d["weights"], WEIGHT_KEYS, "plan.weights")
    assert_keys(d["solver"], SOLVER_KEYS, "plan.solver")
    assert_keys(d["prediction"], PREDICTION_REF_KEYS, "plan.prediction")
    assert_keys(d["totals"], TOTALS_KEYS, "plan.totals")
    for a in d["allocations"]:
        assert_keys(a, ALLOCATION_KEYS, f"allocation {a.get('area_id')}")
        for dl in a["deliveries"]:
            assert_keys(dl, DELIVERY_KEYS, "deliveries[]")
        for r in a["reasons"]:
            assert_keys(r, REASON_KEYS, "reasons[]")
    for r in d["routes"]:
        assert_keys(r, ROUTE_KEYS, f"route {r.get('tanker_id')}")
        for s in r["stops"]:
            assert_keys(s, STOP_KEYS, "stops[]")
    for u in d["unserved"]:
        assert_keys(u, UNSERVED_KEYS, "unserved[]")
    assert_keys(d["constraint_check"], CONSTRAINT_KEYS, "plan.constraint_check")
    for v in d["constraint_check"]["violations"]:
        assert_keys(v, {"code", "detail"}, "violations[]")


# --------------------------------------------------------------------------- datetimes
def assert_iso_datetimes(obj: Any, where: str = "$") -> None:
    """Every string that looks like a datetime has the section 4.3 shape ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert_iso_datetimes(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_iso_datetimes(v, f"{where}[{i}]")
    elif isinstance(obj, str) and ISO_LOOSE_RE.match(obj):
        assert ISO_RE.match(obj), f"{where}: {obj!r} is not UTC ISO-8601 with milliseconds and a Z suffix"


# --------------------------------------------------------------------------- error envelope (section 4.5)
def error_envelope_ok(response, status: int, code: str) -> dict:
    """Assert ``response`` is the section 4.5 envelope with ``status``/``code``; returns the ``error`` object."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert_keys(body, ERROR_ENVELOPE_KEYS, "error envelope")
    err = body["error"]
    assert_keys(err, ERROR_BODY_KEYS, "error")
    assert err["code"] == code, err
    assert isinstance(err["message"], str) and err["message"].strip()
    assert isinstance(err["details"], dict)
    assert REQUEST_RE.match(err["request_id"]), err
    assert response.headers["x-request-id"] == err["request_id"]
    return err


# --------------------------------------------------------------------------- isolated data dir
def copy_real_data(dst: Path) -> Path:
    """Copy the committed seeds, truth files and labels into ``dst``; derived scenarios then land in ``dst/derived``."""
    for sub in ("scenarios", "truth"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
        for src in sorted((REAL_DATA_DIR / sub).glob("*.json")):
            shutil.copyfile(src, dst / sub / src.name)
    labels = REAL_DATA_DIR / "labels.json"
    if labels.is_file():
        shutil.copyfile(labels, dst / "labels.json")
    (dst / "derived").mkdir(exist_ok=True)
    return dst


def real_settings(settings, data_dir: Path, models_dir: Path | None = None):
    """Settings for the REAL app (real seeds + committed model) with auth ENABLED, on an isolated data dir."""
    return settings.model_copy(
        update={
            "a_data_dir": data_dir,
            "a_models_dir": models_dir if models_dir is not None else REAL_MODELS_DIR,
            "a_auth_disabled": False,
        }
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- independent F-1..F-8 / F-15
def _dist(matrix: dict[str, dict[str, float]], a: str, b: str) -> float:
    return matrix[a][b]


def expected_plan(
    scenario: dict, prediction: dict, weights: dict, debt: dict[str, int]
) -> dict[str, Any]:
    """Recompute everything the contract lets us derive independently (F-1..F-8, totals, F-15 count).

    ``scenario`` / ``prediction`` are raw JSON dicts (as served by the API); ``weights`` is the SolveRequest weights.
    Returns ``{"areas": {area_id: {...}}, "order": [area ids by rank], "tankers": {...}}`` with the values the plan
    response must carry. Allocated litres are NOT predicted here (that is the optimizer's choice); callers feed the
    response's allocations to ``recompute_from_allocations``.
    """
    preds = {p["area_id"]: p for p in prediction["areas"]}
    areas = {a["area_id"]: a for a in scenario["areas"]}
    max_debt = max([debt.get(a, 0) for a in areas] + [0])
    out: dict[str, dict[str, Any]] = {}
    for area_id, area in areas.items():
        p = preds[area_id]
        need, piped = p["predicted_need_litres"], p["expected_piped_litres"]
        sf = max(0, need - piped)  # F-1
        u = sf / need if need > 0 else 0.0  # F-2
        d = debt.get(area_id, 0)
        d_hat = d / max(1, max_debt)  # F-3
        v = area["vulnerability_index"] if area["vulnerability_available"] else 0.0  # F-4
        pw = round(1 + weights["urgency"] * u + weights["debt"] * d_hat + weights["vulnerability"] * v, 4)  # F-5
        out[area_id] = {
            "need": need,
            "piped": piped,
            "sf": sf,
            "urgency": round(u, 4),
            "debt": d,
            "debt_normalized": round(d_hat, 4),
            "vulnerability_used": round(v, 4),
            "priority_weight": pw,
        }
    order = sorted(out, key=lambda a: (-out[a]["priority_weight"], -out[a]["sf"], a))
    return {"areas": out, "order": order}


def path_km(matrix: dict[str, dict[str, float]], path: list[str]) -> float:
    """F-8: sum of consecutive distances along ``path`` rounded to 2 dp; 0.0 for an empty path."""
    if not path:
        return 0.0
    return round(sum(matrix[path[k]][path[k + 1]] for k in range(len(path) - 1)), 2)


def count_violations(scenario: dict, plan: dict) -> int:
    """F-15 recomputed from the plan's deliveries and the scenario (independent of ``constraint_check``)."""
    tankers = {t["tanker_id"]: t for t in scenario["tankers"]}
    matrix = scenario["distance_km"]
    n = 0
    allocated = sum(d["litres"] for a in plan["allocations"] for d in a["deliveries"])
    if allocated > scenario["supply"]["tanker_water_available_litres"]:
        n += 1
    load: dict[str, int] = {t: 0 for t in tankers}
    for a in plan["allocations"]:
        for d in a["deliveries"]:
            load[d["tanker_id"]] += d["litres"]
            if tankers[d["tanker_id"]]["status"] == "unavailable":
                n += 1
    for tid, litres in load.items():
        if litres > tankers[tid]["capacity_litres"]:
            n += 1
    for r in plan["routes"]:
        if path_km(matrix, r["path"]) > tankers[r["tanker_id"]]["max_route_km"] + 0.01:
            n += 1
    return n


# --------------------------------------------------------------------------- full formula check on a plan response
def assert_formulas(scenario: dict, prediction: dict, weights: dict, debt: dict[str, int], plan: dict) -> None:
    """Every solve-response field that F-1..F-8, F-12, F-14 and F-15 determine equals the independent recomputation."""
    exp = expected_plan(scenario, prediction, weights, debt)
    areas = {a["area_id"]: a for a in scenario["areas"]}
    tankers = {t["tanker_id"]: t for t in scenario["tankers"]}
    matrix = scenario["distance_km"]
    depot = scenario["depot"]["depot_id"]

    allocs = plan["allocations"]
    assert [a["area_id"] for a in allocs] == exp["order"], "rank order: priority desc, shortfall desc, area_id asc"
    assert [a["rank"] for a in allocs] == list(range(1, len(allocs) + 1))
    for a in allocs:
        e = exp["areas"][a["area_id"]]
        where = f"area {a['area_id']}"
        assert a["predicted_shortfall_litres"] == e["sf"], f"{where}: F-1"
        assert a["urgency"] == e["urgency"], f"{where}: F-2"
        assert a["debt_litres_used"] == e["debt"], where
        assert a["debt_normalized"] == e["debt_normalized"], f"{where}: F-3"
        assert a["vulnerability_used"] == e["vulnerability_used"], f"{where}: F-4"
        assert a["priority_weight"] == e["priority_weight"], f"{where}: F-5"
        assert a["allocated_litres"] == sum(d["litres"] for d in a["deliveries"]), where
        assert 0 <= a["allocated_litres"] <= e["sf"], f"{where}: never more than the predicted shortfall"
        assert a["unmet_litres"] == max(0, e["sf"] - a["allocated_litres"]), f"{where}: F-6"
        cov = 1.0 if e["sf"] == 0 else round(min(1, a["allocated_litres"] / e["sf"]), 4)
        assert a["coverage_ratio"] == cov, f"{where}: F-7"
        assert a["is_unserved_remainder"] == (a["unmet_litres"] > 0), where
        assert a["reasons"] and a["reason_text"].strip(), where
        for key in ("urgency", "priority_weight", "debt_normalized", "vulnerability_used", "coverage_ratio"):
            assert isinstance(a[key], float), f"{where}: {key} is a float on the wire"
        tanker_ids = [d["tanker_id"] for d in a["deliveries"]]
        assert len(tanker_ids) == len(set(tanker_ids)), f"{where}: one delivery per tanker"

    # routes: one per tanker, sorted, F-8 route km, stops <-> deliveries consistency
    routes = plan["routes"]
    assert [r["tanker_id"] for r in routes] == sorted(tankers)
    stops_seen: dict[tuple[str, str], tuple[int, int]] = {}
    for r in routes:
        t = tankers[r["tanker_id"]]
        where = f"route {r['tanker_id']}"
        assert (r["capacity_litres"], r["max_route_km"]) == (t["capacity_litres"], t["max_route_km"]), where
        assert r["route_km"] == path_km(matrix, r["path"]), f"{where}: F-8"
        assert r["load_litres"] == sum(s["litres"] for s in r["stops"]), where
        if t["status"] == "unavailable":
            assert (r["status"], r["stops"], r["path"], r["load_litres"], r["route_km"]) == ("unavailable", [], [], 0, 0.0), where
        elif not r["stops"]:
            assert (r["status"], r["path"], r["route_km"]) == ("idle", [], 0.0), where
        else:
            assert r["status"] == "assigned", where
            assert [s["stop_order"] for s in r["stops"]] == list(range(1, len(r["stops"]) + 1)), where
            assert r["path"] == [depot, *[s["area_id"] for s in r["stops"]], depot], where
            assert len({s["area_id"] for s in r["stops"]}) == len(r["stops"]), f"{where}: one stop per area"
            assert r["load_litres"] <= t["capacity_litres"] and r["route_km"] <= t["max_route_km"] + 0.01, where
            for s in r["stops"]:
                assert s["area_id"] in areas and s["litres"] > 0, where
                stops_seen[(r["tanker_id"], s["area_id"])] = (s["stop_order"], s["litres"])
    deliveries = {(d["tanker_id"], a["area_id"]): (d["stop_order"], d["litres"]) for a in allocs for d in a["deliveries"]}
    assert deliveries == stops_seen, "allocation deliveries and route stops describe the same trips"

    # totals
    totals = plan["totals"]
    assert totals["supply_available_litres"] == scenario["supply"]["tanker_water_available_litres"]
    assert totals["tanker_capacity_available_litres"] == sum(
        t["capacity_litres"] for t in tankers.values() if t["status"] == "available"
    )
    assert totals["shortfall_litres"] == sum(e["sf"] for e in exp["areas"].values())
    assert totals["allocated_litres"] == sum(a["allocated_litres"] for a in allocs)
    assert totals["allocated_litres"] <= totals["supply_available_litres"]
    assert totals["unmet_litres"] == sum(a["unmet_litres"] for a in allocs), "F-12"
    assert totals["travel_km"] == round(sum(r["route_km"] for r in routes), 2), "F-14"

    # unserved list
    assert [u["area_id"] for u in plan["unserved"]] == [a["area_id"] for a in allocs if a["unmet_litres"] > 0]
    for u in plan["unserved"]:
        assert u["unmet_litres"] == next(a["unmet_litres"] for a in allocs if a["area_id"] == u["area_id"])

    # F-15: zero violations, both recomputed here and as reported
    assert count_violations(scenario, plan) == 0
    cc = plan["constraint_check"]
    assert cc["violations"] == []
    assert (cc["water_cap_ok"], cc["capacity_ok"], cc["route_limit_ok"], cc["unavailable_tanker_ok"]) == (True, True, True, True)


# --------------------------------------------------------------------------- fake container (golden / edge tests)
class FakeScenarioRepo:
    """Serves in-memory scenarios (same surface the services use)."""

    def __init__(self, *scenarios: dict):
        from jp_a.domain.entities import Scenario

        self.items = {s["scenario_id"]: Scenario.model_validate(s) for s in scenarios}

    def get(self, scenario_id: str):
        from jp_a.api.errors import ScenarioNotFoundError

        try:
            return self.items[scenario_id].model_copy(deep=True)
        except KeyError:
            raise ScenarioNotFoundError(scenario_id) from None

    def exists(self, scenario_id: str) -> bool:
        return scenario_id in self.items

    def list_summaries(self, include_derived: bool = False):
        return []

    def save_derived(self, scenario) -> None:  # pragma: no cover - not used by these tests
        self.items[scenario.scenario_id] = scenario

    def get_truth(self, scenario_id: str):
        return None


class FakePredictionService:
    """Returns a fixed PredictionSet for the one scenario it was built for."""

    def __init__(self, prediction: dict):
        from jp_a.domain.entities import PredictionSet

        self.prediction = PredictionSet.model_validate(prediction)

    def get_predictions(self, scenario_id: str):
        return self.prediction

    def predict_for_scenario(self, scenario):
        assert scenario.scenario_id == self.prediction.scenario_id
        return self.prediction


class FakeModelRepo:
    model_version = "xgb-sa-v1"

    def load(self) -> None:
        return None

    def is_available(self) -> bool:
        return True


def fake_container(settings, scenario: dict, prediction: dict):
    """A Container wired with the real PlanService / ScenarioService over a tiny fake repo and prediction service."""
    from jp_a.api.deps import Container
    from jp_a.services.plan_service import PlanService
    from jp_a.services.scenario_service import ScenarioService

    repo = FakeScenarioRepo(scenario)
    preds = FakePredictionService(prediction)
    return Container(
        settings=settings,
        scenario_repo=repo,
        model_repo=FakeModelRepo(),
        prediction_service=preds,
        scenario_service=ScenarioService(settings, repo),
        plan_service=PlanService(settings, repo, preds),
    )
