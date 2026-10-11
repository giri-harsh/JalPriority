"""Cross-member end-to-end checklist E1-E20 (INTEGRATION.md section 3.1).

API-level only. Every call goes through one base URL that serves both /a/v1 and
/b/v1 (nginx gateway on :8080, or the Vite dev proxy on :5173).

    JP_BASE_URL=http://localhost:8080 pytest tests -q

Design notes
------------
* Tests are ordered by name (test_E01 ... test_E20) and share results through the
  session-scoped ``state`` dict. A test whose prerequisite is missing skips with a
  reason instead of cascading errors.
* The suite mutates server state (ledger window 6, plan statuses, stored weights,
  events, audit). It is written for a freshly reset demo (scripts/demo_reset) but
  looks everything up dynamically (current window debts, fresh plans) so it can be
  re-run: every test that needs a ``recommended`` plan creates its own.
* Stored fairness weights are reset to the defaults at session start and restored
  after E7, so that plans created without explicit weights behave predictably.
* E8 needs B restarted with B_FF_VULNERABILITY=false and only runs with JP_E8=1.
* E20 asserts the X-Request-ID echo by B and by A. That the id shows up in A's
  logs for the downstream call is verified manually (grep A's stdout for
  req_000000000abc after running the suite).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

import httpx
import pytest

BASE_URL = os.environ.get("JP_BASE_URL", "http://localhost:8080").rstrip("/")
USERNAME = os.environ.get("JP_USER", "officer_01")
PASSWORD = os.environ.get("JP_PASSWORD", "jal-demo-2026")

SCN = "scn_sa01_w06"
DEFAULT_WEIGHTS = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
WEIGHT_KEYS = set(DEFAULT_WEIGHTS)
STRATEGIES = {"jalpriority", "fcfs", "nearest_first"}
REQ_ID_RE = re.compile(r"^req_[0-9a-f]{12}$")
PLAN_ID_RE = re.compile(r"^pln_[0-9a-f]{12}$")
EVT_ID_RE = re.compile(r"^evt_[0-9a-f]{12}$")
AUD_ID_RE = re.compile(r"^aud_[0-9a-f]{12}$")
DERIVED_ID_RE = re.compile(r"^scn_[a-z0-9_]+__d\d+_[0-9a-f]{6}$")
FIXED_REQ_ID = "req_000000000abc"

PLAN_RECORD_KEYS = {
    "schema_version", "plan_id", "status", "created_at", "created_by", "scenario_id",
    "service_area_id", "window_index", "supersedes_plan_id", "replan_of_plan_id",
    "disruption", "is_synthetic", "result", "metrics", "decision",
}
PLAN_RESULT_KEYS = {
    "schema_version", "result_id", "strategy", "scenario_id", "service_area_id",
    "window_index", "generated_at", "weights", "solver", "prediction", "totals",
    "allocations", "routes", "unserved", "constraint_check",
}
ALLOCATION_KEYS = {
    "area_id", "rank", "predicted_shortfall_litres", "allocated_litres", "unmet_litres",
    "coverage_ratio", "priority_weight", "urgency", "debt_litres_used", "debt_normalized",
    "vulnerability_used", "deliveries", "reasons", "reason_text", "is_unserved_remainder",
}
METRICS_KEYS = {
    "schema_version", "priority_weighted_unmet", "deprivation_debt_remaining_litres",
    "total_unmet_litres", "min_coverage_ratio", "total_travel_km", "constraint_violations",
    "replanning_time_ms",
}
ERROR_KEYS = {"code", "message", "details", "request_id"}

EVENT_PROPS = {
    "scenario_loaded": {"n_areas", "supply_litres", "tankers_available"},
    "explanation_viewed": {"plan_id", "area_id"},
    "prediction_generated": {"model_version", "run_mae_litres", "latency_ms"},
    "plan_generated": {"plan_id", "strategy", "weights", "solve_time_ms", "solver_status", "feasible"},
    "weights_changed": {"old_weights", "new_weights"},
    "disruption_applied": {"type", "magnitude", "params", "derived_scenario_id"},
    "replan_completed": {"previous_plan_id", "new_plan_id", "replanning_time_ms", "diff_summary"},
    "comparison_viewed": {"metrics_by_strategy"},
    "plan_decision": {"plan_id", "decision", "officer_id"},
}
EVENT_KEYS = {"schema_version", "event_id", "name", "ts", "officer_id", "scenario_id", "props"}
AUDIT_KEYS = {"audit_id", "ts", "officer_id", "action", "plan_id", "scenario_id", "details"}


# --------------------------------------------------------------------------- helpers

def body_of(resp: httpx.Response, expected: int):
    assert resp.status_code == expected, (
        f"{resp.request.method} {resp.request.url.path} -> {resp.status_code} "
        f"(expected {expected}): {resp.text[:600]}"
    )
    return resp.json()


def error_of(resp: httpx.Response, status: int, code: str | None = None) -> dict:
    assert resp.status_code == status, (
        f"{resp.request.method} {resp.request.url.path} -> {resp.status_code} "
        f"(expected {status}): {resp.text[:600]}"
    )
    data = resp.json()
    assert set(data) == {"error"}, f"not an error envelope: {data}"
    err = data["error"]
    assert ERROR_KEYS <= set(err), f"envelope keys {sorted(err)}"
    assert REQ_ID_RE.match(err["request_id"]) or err["request_id"] == FIXED_REQ_ID, err["request_id"]
    if code is not None:
        assert err["code"] == code, f"expected {code}, got {err}"
    return err


def assert_metrics(m: dict) -> None:
    assert set(m) == METRICS_KEYS, f"Metrics keys differ: {sorted(set(m) ^ METRICS_KEYS)}"
    assert m["schema_version"] == "1.0"


def assert_plan_result(r: dict) -> None:
    assert set(r) == PLAN_RESULT_KEYS, f"PlanResult keys differ: {sorted(set(r) ^ PLAN_RESULT_KEYS)}"
    assert r["schema_version"] == "1.0"
    assert set(r["weights"]) == WEIGHT_KEYS
    for a in r["allocations"]:
        assert set(a) == ALLOCATION_KEYS, f"Allocation keys differ: {sorted(set(a) ^ ALLOCATION_KEYS)}"


def assert_plan_record(p: dict) -> None:
    assert set(p) == PLAN_RECORD_KEYS, f"PlanRecord keys differ: {sorted(set(p) ^ PLAN_RECORD_KEYS)}"
    assert p["schema_version"] == "1.0"
    assert PLAN_ID_RE.match(p["plan_id"]), p["plan_id"]
    assert p["is_synthetic"] is True
    assert_plan_result(p["result"])
    assert_metrics(p["metrics"])


_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|\+00:00)$")


def normalise_timestamps(obj):
    """Canonicalise ISO timestamps to millisecond precision with a Z suffix."""
    if isinstance(obj, dict):
        return {k: normalise_timestamps(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalise_timestamps(v) for v in obj]
    if isinstance(obj, str):
        m = _TS_RE.match(obj)
        if m:
            return f"{m.group(1)}.{(m.group(2) or '0').ljust(3, '0')[:3]}Z"
    return obj


def b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def tamper_token(token: str) -> str:
    """Change the claims but keep the original signature."""
    head, payload, sig = token.split(".")
    claims = json.loads(b64u_decode(payload))
    claims["sub"] = "admin_01"
    claims["role"] = "admin"
    return f"{head}.{b64u_encode(json.dumps(claims, separators=(',', ':')).encode())}.{sig}"


def derived_id_expected(root: str, disruption: dict) -> str:
    applied = [{"type": disruption["type"], "params": disruption["params"]}]
    digest = hashlib.sha1(
        json.dumps(applied, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:6]
    return f"{root}__d1_{digest}"


def f10(prev_debt: int, unmet: int, decay: float) -> set[int]:
    """F-10 candidates: float round (as implemented) or exact half-up."""
    float_round = round((1.0 - decay) * prev_debt + unmet)
    exact = int(
        ((Decimal(1) - Decimal(str(decay))) * Decimal(prev_debt) + Decimal(unmet)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return {float_round, exact}


class Api:
    def __init__(self, client: httpx.Client, token: str):
        self.client = client
        self.token = token

    def headers(self, auth: bool = True, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}"} if auth else {}
        if extra:
            h.update(extra)
        return h

    def request(self, method, path, *, auth=True, headers=None, **kw) -> httpx.Response:
        return self.client.request(method, path, headers=self.headers(auth, headers), **kw)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    # ---- domain shortcuts
    def scenario(self, scenario_id: str = SCN) -> dict:
        return body_of(self.get(f"/b/v1/scenarios/{scenario_id}"), 200)

    def ledger(self, scenario_id: str = SCN) -> dict:
        return body_of(self.get("/b/v1/ledger", params={"scenario_id": scenario_id}), 200)

    def plan(self, plan_id: str) -> dict:
        return body_of(self.get(f"/b/v1/plans/{plan_id}"), 200)

    def new_plan(self, weights: dict | None = DEFAULT_WEIGHTS, time_limit_s: float | None = None,
                 scenario_id: str = SCN) -> tuple[dict, float]:
        payload: dict = {"scenario_id": scenario_id}
        if weights is not None:
            payload["weights"] = weights
        if time_limit_s is not None:
            payload["time_limit_s"] = time_limit_s
        t0 = time.perf_counter()
        resp = self.post("/b/v1/plans", json=payload)
        wall = time.perf_counter() - t0
        return body_of(resp, 201), wall

    def events(self, name: str | None = None) -> list[dict]:
        params = {"limit": 5000}
        if name:
            params["name"] = name
        return body_of(self.get("/b/v1/events", params=params), 200)["events"]

    def audit(self, plan_id: str | None = None) -> list[dict]:
        params = {"limit": 1000}
        if plan_id:
            params["plan_id"] = plan_id
        return body_of(self.get("/b/v1/audit", params=params), 200)["entries"]

    def decide(self, plan_id: str, decision: str, note=None, revised_weights=None) -> httpx.Response:
        payload = {"decision": decision, "note": note, "revised_weights": revised_weights}
        return self.post(f"/b/v1/plans/{plan_id}/decision", json=payload)

    def replan(self, plan_id: str, disruption: dict) -> httpx.Response:
        return self.post("/b/v1/replans", json={"plan_id": plan_id, "disruption": disruption})

    def put_weights(self, service_area_id: str, weights: dict) -> dict:
        return body_of(
            self.put("/b/v1/weights", json={"service_area_id": service_area_id, "weights": weights}), 200
        )

    def fresh_recommended(self, state: dict, key: str) -> dict:
        """Return state[key] if still 'recommended', otherwise create and store a new plan."""
        existing = state.get(key)
        if existing is not None:
            current = self.plan(existing["plan_id"])
            if current["status"] == "recommended":
                return current
        plan, _ = self.new_plan()
        state[key] = plan
        return plan


def need(state: dict, key: str, why: str):
    if key not in state:
        pytest.skip(f"prerequisite missing: {why} (state['{key}'] not set; earlier test failed or was deselected)")
    return state[key]


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="session")
def client():
    c = httpx.Client(base_url=BASE_URL, timeout=60.0)
    try:
        r = c.get("/b/v1/health")
        r.raise_for_status()
        r = c.get("/a/v1/health")
        r.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        c.close()
        pytest.skip(f"stack not reachable at {BASE_URL}: {exc!r}")
    yield c
    c.close()


@pytest.fixture(scope="session")
def token(client):
    r = client.post("/b/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
    data = body_of(r, 200)
    assert data["officer_id"] == USERNAME
    assert data["role"] == "dispatcher"
    assert isinstance(data["token"], str) and data["token"].count(".") == 2
    return data["token"]


@pytest.fixture(scope="session")
def api(client, token):
    return Api(client, token)


@pytest.fixture(scope="session")
def state() -> dict:
    return {}


@pytest.fixture(scope="session", autouse=True)
def _prepare(api, state):
    """Warm A (model load) and put the stored fairness weights back to defaults."""
    api.get("/a/v1/model")
    api.get(f"/b/v1/scenarios/{SCN}/predictions")
    scenario = api.scenario()
    state["service_area_id"] = scenario["service_area_id"]
    api.put_weights(scenario["service_area_id"], DEFAULT_WEIGHTS)
    yield


# --------------------------------------------------------------------------- E1 - E6

def test_E01_auth_crosses_boundary(api, token, state):
    """Login on B -> token works on A. No token -> 401 envelope. Tampered -> 401."""
    ok = api.get("/a/v1/scenarios")
    data = body_of(ok, 200)
    assert any(s["scenario_id"] == SCN for s in data["scenarios"]), "seed scenario missing from A"

    missing = api.get("/a/v1/scenarios", auth=False)
    error_of(missing, 401, "UNAUTHORIZED")

    forged = tamper_token(token)
    bad = api.request("GET", "/a/v1/scenarios", auth=False, headers={"Authorization": f"Bearer {forged}"})
    error_of(bad, 401, "UNAUTHORIZED")

    garbage = api.request("GET", "/a/v1/scenarios", auth=False, headers={"Authorization": "Bearer not.a.jwt"})
    error_of(garbage, 401, "UNAUTHORIZED")

    # B enforces the same rules on its own routes.
    error_of(api.get("/b/v1/scenarios", auth=False), 401, "UNAUTHORIZED")
    bad_b = api.request("GET", "/b/v1/scenarios", auth=False, headers={"Authorization": f"Bearer {forged}"})
    error_of(bad_b, 401, "UNAUTHORIZED")
    state["authenticated"] = True


def test_E02_scenario_passthrough(api, state):
    """GET /b/v1/scenarios/{id} deep-equals GET /a/v1/scenarios/{id}."""
    from_a = body_of(api.get(f"/a/v1/scenarios/{SCN}"), 200)
    from_b = body_of(api.get(f"/b/v1/scenarios/{SCN}"), 200)
    assert from_a == from_b  # strict deep equality, incl. datetime format (CONTRACT 4.3)
    assert from_a["scenario_id"] == SCN
    assert len(from_a["areas"]) == 8 and len(from_a["tankers"]) == 4
    assert from_a["is_synthetic"] is True
    state["scenario"] = from_a


def test_E03_plan_creation(api, state):
    """POST /b/v1/plans -> 201, jalpriority, 0 violations, wall < 10 s."""
    plan, wall = api.new_plan()
    assert_plan_record(plan)
    assert plan["status"] == "recommended"
    assert plan["scenario_id"] == SCN
    assert plan["created_by"] == USERNAME
    assert plan["supersedes_plan_id"] is None and plan["replan_of_plan_id"] is None
    assert plan["disruption"] is None and plan["decision"] is None
    assert plan["result"]["strategy"] == "jalpriority"
    assert plan["metrics"]["constraint_violations"] == 0
    assert plan["result"]["constraint_check"]["violations"] == []
    assert plan["result"]["weights"] == DEFAULT_WEIGHTS
    assert wall < 10.0, f"plan creation took {wall:.2f}s"
    fetched = api.plan(plan["plan_id"])
    assert fetched["plan_id"] == plan["plan_id"]
    state["plan_e3"] = plan


def test_E04_debt_reaches_optimizer(api, state):
    """Every allocation.debt_litres_used equals the ledger debt after window (w-1)."""
    plan = need(state, "plan_e3", "E3 plan")
    ledger = api.ledger()
    prev_window = plan["window_index"] - 1
    assert prev_window == 5, f"expected current window 6, got {plan['window_index']}"
    by_area = {a["area_id"]: a for a in ledger["areas"]}
    assert len(plan["result"]["allocations"]) == 8
    for alloc in plan["result"]["allocations"]:
        area = by_area[alloc["area_id"]]
        point = next((s for s in area["series"] if s["window_index"] == prev_window), None)
        assert point is not None, f"{alloc['area_id']}: no ledger row for window {prev_window}"
        assert alloc["debt_litres_used"] == point["debt_litres"], alloc["area_id"]
        if area["series"][-1]["window_index"] == prev_window:
            assert alloc["debt_litres_used"] == area["current_debt_litres"], alloc["area_id"]
    assert any(a["debt_litres_used"] > 0 for a in plan["result"]["allocations"]), (
        "no area carries debt; the seeded ledger looks empty"
    )


def test_E05_priority_formula_agreement(api, state):
    """Recompute F-5 from the plan's own U, D-hat, V and the plan weights (+-0.0001)."""
    plan = need(state, "plan_e3", "E3 plan")
    result = plan["result"]
    w = result["weights"]
    allocs = result["allocations"]

    for a in allocs:
        p = 1 + w["urgency"] * a["urgency"] + w["debt"] * a["debt_normalized"] + w["vulnerability"] * a["vulnerability_used"]
        # U, D-hat and V are stored rounded to 4 dp; allow the propagated rounding error.
        tol = max(1e-4, 0.5e-4 * (w["urgency"] + w["debt"] + w["vulnerability"]) + 1e-9)
        assert abs(p - a["priority_weight"]) <= tol, (
            f"{a['area_id']}: recomputed {p:.6f} vs priority_weight {a['priority_weight']}"
        )

    # Independent recomputation from unrounded inputs (F-1..F-5).
    preds = {p["area_id"]: p for p in body_of(api.get(f"/b/v1/scenarios/{SCN}/predictions"), 200)["areas"]}
    scenario = state.get("scenario") or api.scenario()
    areas = {x["area_id"]: x for x in scenario["areas"]}
    assert set(preds) == set(areas) == {a["area_id"] for a in allocs}
    dmax = max(1, max(a["debt_litres_used"] for a in allocs))
    for a in allocs:
        pr = preds[a["area_id"]]
        sf = max(0, pr["predicted_need_litres"] - pr["expected_piped_litres"])
        u = sf / pr["predicted_need_litres"] if pr["predicted_need_litres"] > 0 else 0.0
        dhat = a["debt_litres_used"] / dmax
        ar = areas[a["area_id"]]
        v = ar["vulnerability_index"] if ar["vulnerability_available"] else 0.0
        if w["vulnerability"] == 0:
            v = 0.0
        p = round(1 + w["urgency"] * u + w["debt"] * dhat + w["vulnerability"] * v, 4)
        assert a["predicted_shortfall_litres"] == sf, a["area_id"]
        assert abs(p - a["priority_weight"]) <= 1e-4 + 1e-9, (
            f"{a['area_id']}: F-5 from unrounded inputs {p} vs priority_weight {a['priority_weight']}"
        )


def test_E06_metrics_agreement(state):
    """B's metrics agree with A's totals."""
    plan = need(state, "plan_e3", "E3 plan")
    totals = plan["result"]["totals"]
    m = plan["metrics"]
    assert m["total_unmet_litres"] == totals["unmet_litres"]
    assert abs(m["total_travel_km"] - totals["travel_km"]) <= 0.005 + 1e-9
    assert totals["unmet_litres"] == sum(a["unmet_litres"] for a in plan["result"]["allocations"])
    assert totals["allocated_litres"] == sum(a["allocated_litres"] for a in plan["result"]["allocations"])
    assert totals["allocated_litres"] <= totals["supply_available_litres"]


# --------------------------------------------------------------------------- E7 - E8

def test_E07_weights_change_the_plan(api, state):
    """PUT weights with debt = 0, regenerate -> plan or priorities differ, weights_changed stored."""
    base = need(state, "plan_e3", "E3 plan")
    sa = state["service_area_id"]
    before_ids = {e["event_id"] for e in api.events("weights_changed")}
    zero_debt = {**DEFAULT_WEIGHTS, "debt": 0.0}
    try:
        saved = api.put_weights(sa, zero_debt)
        assert saved["weights"] == zero_debt
        assert saved["service_area_id"] == sa
        assert saved["defaults"] == DEFAULT_WEIGHTS
        assert set(saved["flags"]) == {"fairness_weights", "vulnerability", "replan"}

        # No explicit weights: B must use the stored ones.
        new, _ = api.new_plan(weights=None)
        assert_plan_record(new)
        assert new["result"]["weights"]["debt"] == 0.0
        assert new["metrics"]["constraint_violations"] == 0

        prio_old = {a["area_id"]: a["priority_weight"] for a in base["result"]["allocations"]}
        prio_new = {a["area_id"]: a["priority_weight"] for a in new["result"]["allocations"]}
        alloc_old = {a["area_id"]: a["allocated_litres"] for a in base["result"]["allocations"]}
        alloc_new = {a["area_id"]: a["allocated_litres"] for a in new["result"]["allocations"]}
        assert prio_old != prio_new or alloc_old != alloc_new, "debt weight 0 changed nothing"
        assert all(a["debt_normalized"] >= 0 for a in new["result"]["allocations"])
        state["plan_e7"] = new

        fresh = [e for e in api.events("weights_changed") if e["event_id"] not in before_ids]
        assert fresh, "no weights_changed event stored"
        assert any(
            e["props"]["new_weights"]["debt"] == 0.0 and e["props"]["old_weights"]["debt"] == 1.0
            for e in fresh
        ), f"unexpected weights_changed props: {[e['props'] for e in fresh]}"
    finally:
        api.put_weights(sa, DEFAULT_WEIGHTS)


@pytest.mark.skipif(
    os.environ.get("JP_E8") != "1",
    reason="manual: requires restarting B with B_FF_VULNERABILITY=false",
)
def test_E08_vulnerability_flag_off(api):
    """With B_FF_VULNERABILITY=false every vulnerability_used is 0 in A's result (JP_E8=1)."""
    plan, _ = api.new_plan()
    assert plan["result"]["weights"]["vulnerability"] == 0
    assert all(a["vulnerability_used"] == 0 for a in plan["result"]["allocations"])
    flags = body_of(api.get("/b/v1/weights", params={"service_area_id": plan["service_area_id"]}), 200)["flags"]
    assert flags["vulnerability"] is False


# --------------------------------------------------------------------------- E9 - E11

def test_E09_approve_updates_ledger(api, state):
    """Approve -> window 6 ledger rows (approved_plan) equal F-10 of window-5 debt and plan unmet."""
    plan = api.fresh_recommended(state, "plan_e3")
    state["plan_e3"] = plan
    ledger_before = api.ledger()
    assert ledger_before["decay"] == 0.3, f"lambda is {ledger_before['decay']}, test assumes 0.3"
    win = plan["window_index"]
    prev = {}
    for area in ledger_before["areas"]:
        point = next(s for s in area["series"] if s["window_index"] == win - 1)
        prev[area["area_id"]] = point["debt_litres"]

    resp = body_of(api.decide(plan["plan_id"], "approved", note="e2e approval"), 200)
    assert set(resp) == {"plan", "next_plan", "ledger_updated"}
    assert resp["ledger_updated"] is True
    assert resp["next_plan"] is None
    assert_plan_record(resp["plan"])
    assert resp["plan"]["status"] == "approved"
    assert resp["plan"]["decision"]["decision"] == "approved"
    assert resp["plan"]["decision"]["officer_id"] == USERNAME
    assert api.plan(plan["plan_id"])["status"] == "approved"

    ledger = api.ledger()
    assert win in ledger["windows"], f"window {win} missing: {ledger['windows']}"
    by_area = {a["area_id"]: a for a in ledger["areas"]}
    for alloc in plan["result"]["allocations"]:
        rows = [s for s in by_area[alloc["area_id"]]["series"] if s["window_index"] == win]
        assert len(rows) == 1, f"{alloc['area_id']}: {len(rows)} rows for window {win}"
        row = rows[0]
        assert row["source"] == "approved_plan", alloc["area_id"]
        assert row["plan_id"] == plan["plan_id"], alloc["area_id"]
        assert row["unmet_litres"] == alloc["unmet_litres"], alloc["area_id"]
        assert row["debt_litres"] in f10(prev[alloc["area_id"]], alloc["unmet_litres"], 0.3), (
            f"{alloc['area_id']}: debt {row['debt_litres']} vs F-10({prev[alloc['area_id']]}, "
            f"{alloc['unmet_litres']})"
        )
    # Earlier windows are untouched.
    for area in ledger_before["areas"]:
        old = [s for s in area["series"] if s["window_index"] < win]
        new = [s for s in by_area[area["area_id"]]["series"] if s["window_index"] < win]
        assert old == new
    state["plan_approved"] = plan


def test_E10_edit_supersedes(api, state):
    """Decision 'edited' with revised weights -> next_plan supersedes the old one."""
    plan, _ = api.new_plan()
    revised = {"urgency": 1.0, "debt": 2.0, "vulnerability": 1.0, "travel": 20.0}

    # revised_weights is mandatory for 'edited'
    error_of(api.decide(plan["plan_id"], "edited", note="no weights"), 422, "VALIDATION_ERROR")
    assert api.plan(plan["plan_id"])["status"] == "recommended"

    resp = body_of(api.decide(plan["plan_id"], "edited", note="raise debt weight", revised_weights=revised), 200)
    assert resp["ledger_updated"] is False
    old, nxt = resp["plan"], resp["next_plan"]
    assert nxt is not None
    assert_plan_record(old)
    assert_plan_record(nxt)
    assert nxt["supersedes_plan_id"] == plan["plan_id"] == old["plan_id"]
    assert old["status"] == "edited"
    assert old["decision"]["decision"] == "edited"
    assert old["decision"]["next_plan_id"] == nxt["plan_id"]
    assert nxt["status"] == "recommended"
    assert nxt["result"]["weights"] == revised
    assert nxt["metrics"]["constraint_violations"] == 0
    assert api.plan(plan["plan_id"])["status"] == "edited"
    state["plan_edited"] = old
    state["plan_edit_next"] = nxt


def test_E11_reject_then_conflict(api, state):
    """Reject with a note -> status rejected; a second decision -> 409."""
    plan, _ = api.new_plan()
    error_of(api.decide(plan["plan_id"], "rejected"), 422, "VALIDATION_ERROR")
    assert api.plan(plan["plan_id"])["status"] == "recommended"

    note = "Route plan ignores a closed bridge"
    resp = body_of(api.decide(plan["plan_id"], "rejected", note=note), 200)
    assert resp["plan"]["status"] == "rejected"
    assert resp["plan"]["decision"]["decision"] == "rejected"
    assert resp["plan"]["decision"]["note"] == note
    assert resp["next_plan"] is None
    assert resp["ledger_updated"] is False
    assert api.plan(plan["plan_id"])["status"] == "rejected"

    second = api.decide(plan["plan_id"], "approved", note="too late")
    error_of(second, 409, "PLAN_ALREADY_DECIDED")
    error_of(api.decide(plan["plan_id"], "rejected", note="again"), 409, "PLAN_ALREADY_DECIDED")
    assert api.plan(plan["plan_id"])["status"] == "rejected"
    state["plan_rejected"] = resp["plan"]


# --------------------------------------------------------------------------- E12

def test_E12_comparison(api, state):
    """3 strategies on the same scenario and supply; JalPriority beats both baselines."""
    resp = body_of(api.post("/b/v1/comparisons", json={"scenario_id": SCN, "weights": DEFAULT_WEIGHTS}), 200)
    assert resp["scenario_id"] == SCN
    assert resp["is_simulation"] is True
    assert set(resp["results"]) == STRATEGIES
    assert set(resp["metrics"]) == STRATEGIES
    assert PLAN_ID_RE.match(resp["plan_id"])
    for name, res in resp["results"].items():
        assert_plan_result(res)
        assert res["strategy"] == name
        assert res["scenario_id"] == SCN
        assert_metrics(resp["metrics"][name])
    supplies = {res["totals"]["supply_available_litres"] for res in resp["results"].values()}
    assert len(supplies) == 1, f"strategies used different supply: {supplies}"
    assert resp["results"]["jalpriority"]["solver"]["engine"] != "baseline-fcfs"

    m = resp["metrics"]
    jp, fcfs, nn = m["jalpriority"], m["fcfs"], m["nearest_first"]
    assert jp["priority_weighted_unmet"] < fcfs["priority_weighted_unmet"], (jp, fcfs)
    assert jp["priority_weighted_unmet"] < nn["priority_weighted_unmet"], (jp, nn)
    assert jp["min_coverage_ratio"] >= fcfs["min_coverage_ratio"]
    assert jp["min_coverage_ratio"] >= nn["min_coverage_ratio"]
    assert jp["constraint_violations"] == fcfs["constraint_violations"] == nn["constraint_violations"] == 0
    assert set(resp["best_by_metric"]) >= {"priority_weighted_unmet", "min_coverage_ratio", "constraint_violations"}
    state["comparison"] = resp

    # The Compare screen reports what it displayed; B does not emit this event itself.
    evt = body_of(
        api.post("/b/v1/events", json={
            "name": "comparison_viewed", "scenario_id": SCN, "props": {"metrics_by_strategy": m},
        }),
        202,
    )
    assert EVT_ID_RE.match(evt["event_id"])


# --------------------------------------------------------------------------- E13 - E16

def _replan_checks(api, resp_json: dict, old_plan: dict, disruption: dict) -> dict:
    assert resp_json["schema_version"] == "1.0"
    assert resp_json["previous_plan_id"] == old_plan["plan_id"]
    assert DERIVED_ID_RE.match(resp_json["derived_scenario_id"]), resp_json["derived_scenario_id"]
    assert resp_json["disruption"] == disruption
    new = resp_json["new_plan"]
    assert_plan_record(new)
    assert new["replan_of_plan_id"] == old_plan["plan_id"]
    assert new["disruption"] == disruption
    assert new["scenario_id"] == resp_json["derived_scenario_id"]
    assert new["status"] == "recommended"
    assert new["result"]["strategy"] == "jalpriority"
    assert new["metrics"]["constraint_violations"] == 0
    assert new["result"]["constraint_check"]["violations"] == []
    assert resp_json["replanning_time_ms"] < 5000, f"replan took {resp_json['replanning_time_ms']} ms"
    assert new["metrics"]["replanning_time_ms"] == resp_json["replanning_time_ms"]
    diff = resp_json["diff"]
    assert set(diff) == {"areas", "summary"}
    assert set(diff["summary"]) == {"areas_changed", "litres_moved", "metrics_before", "metrics_after"}
    return new


def test_E13_replan_tanker_down(api, state):
    """tanker_down on an available tanker: derived id, no deliveries, < 5 s, diff, old superseded."""
    plan, _ = api.new_plan()
    scenario = api.scenario()
    available = {t["tanker_id"] for t in scenario["tankers"] if t["status"] == "available"}
    assert available, "scenario has no available tanker"
    loads = {r["tanker_id"]: r["load_litres"] for r in plan["result"]["routes"] if r["status"] == "assigned"}
    candidates = [t for t in sorted(loads, key=lambda k: -loads[k]) if t in available]
    tanker = candidates[0] if candidates else sorted(available)[0]
    disruption = {"type": "tanker_down", "params": {"tanker_id": tanker}}

    t0 = time.perf_counter()
    raw = api.replan(plan["plan_id"], disruption)
    wall = time.perf_counter() - t0
    data = body_of(raw, 201)
    new = _replan_checks(api, data, plan, disruption)

    assert data["derived_scenario_id"] == derived_id_expected(SCN, disruption)
    for alloc in new["result"]["allocations"]:
        assert all(d["tanker_id"] != tanker for d in alloc["deliveries"]), (
            f"{alloc['area_id']} still served by {tanker}"
        )
    route = next(r for r in new["result"]["routes"] if r["tanker_id"] == tanker)
    assert route["status"] == "unavailable" and route["stops"] == [] and route["path"] == []
    assert new["result"]["constraint_check"]["unavailable_tanker_ok"] is True
    assert data["diff"]["areas"], "diff is empty"
    assert data["diff"]["summary"]["areas_changed"] >= 1
    assert wall < 8.0, f"replan wall time {wall:.2f}s"

    assert api.plan(plan["plan_id"])["status"] == "superseded"

    derived = body_of(api.get(f"/a/v1/scenarios/{data['derived_scenario_id']}"), 200)
    assert derived["parent_scenario_id"] == SCN
    assert next(t for t in derived["tankers"] if t["tanker_id"] == tanker)["status"] == "unavailable"

    state["replan_tanker"] = {"old": plan, "new": new, "tanker": tanker, "disruption": disruption}


def test_E14_replan_supply_cut(api, state):
    """supply_cut 0.3: S' = floor(S*0.7/100)*100 (decimal arithmetic); unmet increases."""
    plan, _ = api.new_plan()
    supply = plan["result"]["totals"]["supply_available_litres"]
    expected = int(
        ((Decimal(supply) * (Decimal(1) - Decimal("0.3"))) / Decimal(100)).to_integral_value(rounding=ROUND_FLOOR)
    ) * 100
    disruption = {"type": "supply_cut", "params": {"fraction": 0.3}}
    data = body_of(api.replan(plan["plan_id"], disruption), 201)
    new = _replan_checks(api, data, plan, disruption)

    assert new["result"]["totals"]["supply_available_litres"] == expected, (
        f"S={supply}: expected {expected}, got {new['result']['totals']['supply_available_litres']}"
    )
    assert new["result"]["totals"]["unmet_litres"] > plan["result"]["totals"]["unmet_litres"], (
        "total unmet did not increase after a 30% supply cut"
    )
    assert new["result"]["totals"]["allocated_litres"] <= expected
    assert data["diff"]["areas"]
    assert api.plan(plan["plan_id"])["status"] == "superseded"
    state["replan_supply"] = {"old": plan, "new": new}


def test_E15_replan_heat_alert(api, state):
    """heat_alert level +1: derived alert level +1 and the predictions change."""
    plan, _ = api.new_plan()
    scenario = api.scenario()
    level = scenario["heat"]["alert_level"]
    if level >= 3:
        pytest.skip("seed scenario is already at heat alert level 3; cannot raise by +1")
    disruption = {"type": "heat_alert", "params": {"alert_level": level + 1}}
    data = body_of(api.replan(plan["plan_id"], disruption), 201)
    new = _replan_checks(api, data, plan, disruption)

    derived = api.scenario(data["derived_scenario_id"])
    assert derived["heat"]["alert_level"] == level + 1
    assert derived["parent_scenario_id"] == SCN
    assert abs(derived["heat"]["max_temp_c"] - (scenario["heat"]["max_temp_c"] + 1.5)) <= 0.051
    assert abs(derived["heat"]["heat_index_c"] - (scenario["heat"]["heat_index_c"] + 2.5)) <= 0.051
    assert derived["disruptions_applied"][-1]["type"] == "heat_alert"

    pred_parent = body_of(api.get(f"/b/v1/scenarios/{SCN}/predictions"), 200)["areas"]
    pred_child = body_of(api.get(f"/b/v1/scenarios/{data['derived_scenario_id']}/predictions"), 200)["areas"]
    need_parent = {p["area_id"]: p["predicted_need_litres"] for p in pred_parent}
    need_child = {p["area_id"]: p["predicted_need_litres"] for p in pred_child}
    assert need_parent != need_child, "predictions did not change after the heat alert"
    assert new["result"]["totals"]["shortfall_litres"] != plan["result"]["totals"]["shortfall_litres"]
    state["replan_heat"] = {"old": plan, "new": new}


def test_E16_disruption_errors_pass_through(api, state):
    """A tanker that is already down -> 502 UPSTREAM_A_ERROR with upstream DISRUPTION_NOT_APPLICABLE."""
    info = need(state, "replan_tanker", "E13 replan (its derived plan already has the tanker down)")
    plan = info["new"]
    disruption = {"type": "tanker_down", "params": {"tanker_id": info["tanker"]}}
    err = error_of(api.replan(plan["plan_id"], disruption), 502, "UPSTREAM_A_ERROR")
    upstream = err["details"]["upstream"]
    assert upstream["code"] == "DISRUPTION_NOT_APPLICABLE", upstream
    assert api.plan(plan["plan_id"])["status"] == "recommended", "failed replan must not touch the plan"

    # Unknown tanker: A answers 422 DISRUPTION_INVALID, B wraps it the same way.
    bad = {"type": "tanker_down", "params": {"tanker_id": "tk_99"}}
    err = error_of(api.replan(plan["plan_id"], bad), 502, "UPSTREAM_A_ERROR")
    assert err["details"]["upstream"]["code"] == "DISRUPTION_INVALID", err["details"]

    # SCENARIO_NOT_FOUND is the documented exception: passed through as 404.
    error_of(api.get("/b/v1/scenarios/scn_sa01_w99"), 404, "SCENARIO_NOT_FOUND")


# --------------------------------------------------------------------------- E17 - E18

def _check_event_props(evt: dict) -> None:
    name, props = evt["name"], evt["props"]
    assert name in EVENT_PROPS, f"unknown event name stored: {name}"
    assert set(props) == EVENT_PROPS[name], (
        f"{name} props keys {sorted(props)} != {sorted(EVENT_PROPS[name])}"
    )
    if name == "plan_generated":
        assert set(props["weights"]) == WEIGHT_KEYS
        assert props["strategy"] in STRATEGIES
    if name == "weights_changed":
        assert set(props["old_weights"]) == WEIGHT_KEYS and set(props["new_weights"]) == WEIGHT_KEYS
    if name == "replan_completed":
        assert set(props["diff_summary"]) == {"areas_changed", "litres_moved"}
    if name == "comparison_viewed":
        assert set(props["metrics_by_strategy"]) == STRATEGIES
        for m in props["metrics_by_strategy"].values():
            assert set(m) == METRICS_KEYS
    if name == "disruption_applied":
        assert props["type"] in {"tanker_down", "supply_cut", "heat_alert"}
        assert DERIVED_ID_RE.match(props["derived_scenario_id"])


def test_E17_events_complete(api, state):
    """All 9 event names are stored with exactly the section 7 props keys."""
    scenario = state.get("scenario") or api.scenario()
    plan = state.get("plan_e3") or api.new_plan()[0]
    # The two A-emitted events reach B through POST /b/v1/events (the shell's onTrack).
    shell_events = [
        {
            "name": "scenario_loaded",
            "scenario_id": SCN,
            "props": {
                "n_areas": len(scenario["areas"]),
                "supply_litres": scenario["supply"]["tanker_water_available_litres"],
                "tankers_available": sum(1 for t in scenario["tankers"] if t["status"] == "available"),
            },
        },
        {
            "name": "explanation_viewed",
            "scenario_id": SCN,
            "props": {"plan_id": plan["plan_id"], "area_id": plan["result"]["allocations"][0]["area_id"]},
        },
    ]
    for payload in shell_events:
        r = body_of(api.post("/b/v1/events", json=payload), 202)
        assert EVT_ID_RE.match(r["event_id"])

    # Unknown names are refused.
    refused = api.post("/b/v1/events", json={"name": "not_an_event", "scenario_id": None, "props": {}})
    assert refused.status_code == 422, refused.text

    events = api.events()
    by_name: dict[str, list[dict]] = {}
    for e in events:
        assert set(e) == EVENT_KEYS, f"Event keys differ: {sorted(set(e) ^ EVENT_KEYS)}"
        assert EVT_ID_RE.match(e["event_id"])
        assert e["schema_version"] == "1.0"
        by_name.setdefault(e["name"], []).append(e)
    missing = sorted(set(EVENT_PROPS) - set(by_name))
    assert not missing, f"event names never stored: {missing}"
    for e in events:
        _check_event_props(e)

    # Spot checks that tie events to this run's flows.
    gen = by_name["plan_generated"]
    assert any(e["props"]["plan_id"] == plan["plan_id"] for e in gen)
    assert {"fcfs", "nearest_first"} <= {e["props"]["strategy"] for e in gen if e["props"]["plan_id"] is None}
    decisions = {e["props"]["decision"] for e in by_name["plan_decision"]}
    assert {"approved", "edited", "rejected"} <= decisions
    assert any(e["props"]["replanning_time_ms"] < 5000 for e in by_name["replan_completed"])
    magnitudes = {e["props"]["type"]: e["props"]["magnitude"] for e in by_name["disruption_applied"]}
    if "tanker_down" in magnitudes:
        assert magnitudes["tanker_down"] == 1
    if "supply_cut" in magnitudes:
        assert magnitudes["supply_cut"] == 0.3
    only_scn = api.events("scenario_loaded")
    assert any(e["scenario_id"] == SCN for e in only_scn)


def test_E18_audit_trail(api, state):
    """Audit has plan_generated, plan_approved, plan_edited, plan_rejected, weights_changed, replan_applied."""
    entries = api.audit()
    for e in entries:
        assert set(e) == AUDIT_KEYS, f"Audit keys differ: {sorted(set(e) ^ AUDIT_KEYS)}"
        assert AUD_ID_RE.match(e["audit_id"])
    actions = {e["action"] for e in entries}
    required = {"plan_generated", "plan_approved", "plan_edited", "plan_rejected", "weights_changed", "replan_applied"}
    assert required <= actions, f"missing audit actions: {sorted(required - actions)}"

    for key, action in (
        ("plan_approved", "plan_approved"),
        ("plan_edited", "plan_edited"),
        ("plan_rejected", "plan_rejected"),
    ):
        plan = state.get(key)
        if plan is None:
            continue
        rows = api.audit(plan_id=plan["plan_id"])
        assert any(r["action"] == action and r["officer_id"] == USERNAME for r in rows), (
            f"no {action} audit entry for {plan['plan_id']}"
        )
    info = state.get("replan_tanker")
    if info is not None:
        rows = api.audit(plan_id=info["new"]["plan_id"])
        assert any(r["action"] == "replan_applied" for r in rows)


# --------------------------------------------------------------------------- E19 - E20

def test_E19_time_limited_solve(api):
    """time_limit_s = 0.5 -> acceptable solver status and zero violations."""
    plan, wall = api.new_plan(time_limit_s=0.5)
    assert_plan_record(plan)
    assert plan["result"]["solver"]["status"] in {"time_limited", "optimal", "heuristic"}, plan["result"]["solver"]
    assert plan["result"]["solver"]["time_limit_s"] == 0.5
    assert plan["result"]["constraint_check"]["violations"] == []
    assert plan["metrics"]["constraint_violations"] == 0
    # A promises to answer within time_limit_s + 1.0 s; allow for B and the gateway.
    assert wall < 5.0, f"time-limited plan took {wall:.2f}s"


def test_E20_request_id_echo(api):
    """X-Request-ID req_000000000abc is echoed by B and by A (through the same base).

    The id must also appear in A's logs for the downstream call (B -> A). That is asserted when A's
    stdout log is reachable (JP_A_LOG, default integration/.run/a.log written by dev_local.ps1); with
    Docker, grep `docker compose logs jp-a-api` for req_000000000abc instead.
    """
    hdr = {"X-Request-ID": FIXED_REQ_ID}

    r = api.get("/b/v1/health", auth=False, headers=hdr)
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == FIXED_REQ_ID

    # Authenticated B route that makes a downstream call to A.
    r = api.get(f"/b/v1/scenarios/{SCN}", headers=hdr)
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == FIXED_REQ_ID

    log_path = os.environ.get("JP_A_LOG") or os.path.join(os.path.dirname(__file__), "..", ".run", "a.log")
    if os.path.exists(log_path):
        deadline = time.time() + 5
        found = False
        while time.time() < deadline and not found:
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                found = FIXED_REQ_ID in fh.read()
            if not found:
                time.sleep(0.3)
        assert found, "B did not forward X-Request-ID to A (not found in A's log)"

    # Error envelope carries the same id.
    r = api.get("/b/v1/plans/pln_000000000000", headers=hdr)
    err = error_of(r, 404, "PLAN_NOT_FOUND")
    assert err["request_id"] == FIXED_REQ_ID
    assert r.headers.get("x-request-id") == FIXED_REQ_ID

    # Direct A health through the same base.
    r = api.get("/a/v1/health", auth=False, headers=hdr)
    data = body_of(r, 200)
    assert data["status"] == "ok" and data["service"] == "jp-a"
    assert r.headers.get("x-request-id") == FIXED_REQ_ID

    # A's error envelope too.
    r = api.get("/a/v1/scenarios/scn_sa01_w99", headers=hdr)
    err = error_of(r, 404, "SCENARIO_NOT_FOUND")
    assert err["request_id"] == FIXED_REQ_ID
    assert r.headers.get("x-request-id") == FIXED_REQ_ID

    # Without a header both services generate a well-formed id.
    r = api.get("/a/v1/health", auth=False)
    assert REQ_ID_RE.match(r.headers.get("x-request-id", ""))
    r = api.get("/b/v1/health", auth=False)
    assert REQ_ID_RE.match(r.headers.get("x-request-id", ""))
