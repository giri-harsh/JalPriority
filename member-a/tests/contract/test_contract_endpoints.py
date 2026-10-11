"""MEMBER_A section 6 item 2: live endpoints 6.1-6.7 of the REAL app (real seeds + committed model, auth enabled).

Covers strict parsing, exact key sets, id patterns, datetime shape (Z + milliseconds), the section 4.5 error
envelope with its codes and statuses, 503 MODEL_UNAVAILABLE and X-Request-ID echo / reuse.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from jp_a.domain.entities import ModelInfo, PlanResult, PredictionSet, Scenario, ScenarioSummary
from jp_a.main import create_app

from .contract_helpers import (
    API,
    AREA_RE,
    DEPOT_RE,
    HEALTH_KEYS,
    ISO_RE,
    REQUEST_RE,
    RESULT_RE,
    SCENARIO_LIST_KEYS,
    SCENARIO_SUMMARY_KEYS,
    SEEDS,
    SERVICE_AREA_RE,
    TANKER_RE,
    ALLOWED_FEATURES,
    REASON_CODES,
    UNSERVED_REASON_CODES,
    assert_iso_datetimes,
    assert_keys,
    assert_model_info_keys,
    assert_plan_keys,
    assert_prediction_keys,
    assert_scenario_keys,
    bearer,
    copy_real_data,
    error_envelope_ok,
    is_scenario_id,
    real_settings,
)

SOLVE_WEIGHTS = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return copy_real_data(tmp_path_factory.mktemp("contract_endpoints_data"))


@pytest.fixture(scope="module")
def api(settings, data_dir):
    with TestClient(create_app(real_settings(settings, data_dir))) as c:
        yield c


@pytest.fixture(scope="module")
def hdr(make_token):
    return bearer(make_token())


def solve_body(scenario_id: str = "scn_sa01_w06", **over) -> dict:
    body = {
        "scenario_id": scenario_id,
        "weights": dict(SOLVE_WEIGHTS),
        "debt_by_area": {"ar_01": 0, "ar_02": 8000, "ar_03": 12000},
        "time_limit_s": 2.0,
    }
    body.update(over)
    return body


# (method, path, json body) for every protected route
PROTECTED = [
    ("GET", f"{API}/scenarios", None),
    ("GET", f"{API}/scenarios/scn_sa01_w06", None),
    ("POST", f"{API}/scenarios/scn_sa01_w06/disruptions", {"type": "tanker_down", "params": {"tanker_id": "tk_01"}}),
    ("GET", f"{API}/scenarios/scn_sa01_w06/predictions", None),
    ("POST", f"{API}/plans/solve", solve_body()),
    ("GET", f"{API}/model", None),
]


def call(api, method: str, path: str, body=None, headers=None):
    return api.request(method, path, json=body, headers=headers)


# --------------------------------------------------------------------------- 6.1 health
def test_health_is_public_and_exact(api):
    r = api.get(f"{API}/health")
    assert r.status_code == 200
    body = r.json()
    assert_keys(body, HEALTH_KEYS, "health")
    assert body["status"] == "ok"
    assert body["service"] == "jp-a"
    assert body["version"] == "1.0.0"
    assert body["model_version"] == "xgb-sa-v1"
    assert ISO_RE.match(body["time"])


def test_health_ignores_a_bad_token(api):
    assert api.get(f"{API}/health", headers=bearer("garbage")).status_code == 200


# --------------------------------------------------------------------------- 6.2 scenario list
def test_scenario_list_shape_and_order(api, hdr):
    r = api.get(f"{API}/scenarios", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert_keys(body, SCENARIO_LIST_KEYS, "scenario list")
    ids = [s["scenario_id"] for s in body["scenarios"]]
    assert ids == sorted(ids)
    assert set(SEEDS) <= set(ids)
    for item in body["scenarios"]:
        assert_keys(item, SCENARIO_SUMMARY_KEYS, f"summary {item['scenario_id']}")
        ScenarioSummary.model_validate(item)
        assert is_scenario_id(item["scenario_id"])
        assert SERVICE_AREA_RE.match(item["service_area_id"])
        assert item["parent_scenario_id"] is None
        assert item["is_synthetic"] is True
        assert item["schema_version"] == "1.0"
        assert isinstance(item["label"], str) and item["label"]
    assert_iso_datetimes(body)


def test_scenario_summaries_match_scenarios(api, hdr):
    summaries = {s["scenario_id"]: s for s in api.get(f"{API}/scenarios", headers=hdr).json()["scenarios"]}
    for sid in SEEDS:
        sc = api.get(f"{API}/scenarios/{sid}", headers=hdr).json()
        s = summaries[sid]
        assert s["n_areas"] == len(sc["areas"]) == 8
        assert s["supply_litres"] == sc["supply"]["tanker_water_available_litres"]
        assert s["tankers_available"] == sum(t["status"] == "available" for t in sc["tankers"])
        assert s["alert_level"] == sc["heat"]["alert_level"]
        assert s["window_index"] == sc["window_index"]
        assert s["service_area_id"] == sc["service_area_id"]


def test_include_derived_flag(api, hdr):
    created = api.post(
        f"{API}/scenarios/scn_sa01_w06/disruptions",
        json={"type": "tanker_down", "params": {"tanker_id": "tk_04"}},
        headers=hdr,
    )
    assert created.status_code in (200, 201)
    derived_id = created.json()["scenario_id"]
    for url in (f"{API}/scenarios", f"{API}/scenarios?include_derived=false"):
        ids = [s["scenario_id"] for s in api.get(url, headers=hdr).json()["scenarios"]]
        assert derived_id not in ids
    items = api.get(f"{API}/scenarios?include_derived=true", headers=hdr).json()["scenarios"]
    ids = [s["scenario_id"] for s in items]
    assert ids == sorted(ids)
    assert derived_id in ids and set(SEEDS) <= set(ids)
    item = next(s for s in items if s["scenario_id"] == derived_id)
    assert_keys(item, SCENARIO_SUMMARY_KEYS, "derived summary")
    ScenarioSummary.model_validate(item)
    assert item["parent_scenario_id"] == "scn_sa01_w06"
    assert item["tankers_available"] == 3


def test_include_derived_rejects_non_bool(api, hdr):
    r = api.get(f"{API}/scenarios?include_derived=maybe", headers=hdr)
    err = error_envelope_ok(r, 422, "VALIDATION_ERROR")
    assert err["details"]["errors"]


# --------------------------------------------------------------------------- 6.3 scenario
@pytest.mark.parametrize("sid", SEEDS)
def test_scenario_strict_keys_ids_and_datetimes(api, hdr, sid):
    r = api.get(f"{API}/scenarios/{sid}", headers=hdr)
    assert r.status_code == 200
    d = r.json()
    Scenario.model_validate(d)
    assert_scenario_keys(d)
    assert_iso_datetimes(d)

    assert d["schema_version"] == "1.0"
    assert d["scenario_id"] == sid and is_scenario_id(sid) and "__" not in sid
    assert d["parent_scenario_id"] is None
    assert d["disruptions_applied"] == []
    assert SERVICE_AREA_RE.match(d["service_area_id"])
    assert d["window_hours"] == 12 and d["window_index"] == 6
    assert d["is_synthetic"] is True
    assert isinstance(d["seed"], int)
    assert 0 <= d["heat"]["alert_level"] <= 3
    assert isinstance(d["supply"]["tanker_water_available_litres"], int)
    assert DEPOT_RE.match(d["depot"]["depot_id"])

    assert len(d["tankers"]) == 4 and len(d["areas"]) == 8
    for t in d["tankers"]:
        assert TANKER_RE.match(t["tanker_id"])
        assert t["status"] in {"available", "unavailable"}
        assert 10_000 <= t["capacity_litres"] <= 12_000
        assert t["max_route_km"] == 60.0

    area_ids = [a["area_id"] for a in d["areas"]]
    assert len(set(area_ids)) == len(area_ids)
    for a in d["areas"]:
        assert AREA_RE.match(a["area_id"])
        assert 0.0 <= a["vulnerability_index"] <= 1.0 and 0.0 <= a["heat_exposure"] <= 1.0
        assert ISO_RE.match(a["request_time"])
        assert a["last_delivery_at"] is None or ISO_RE.match(a["last_delivery_at"])
        windows = [h["window_index"] for h in a["history"]]
        assert windows == [1, 2, 3, 4, 5]
        assert all(w < d["window_index"] for w in windows)


@pytest.mark.parametrize("sid", SEEDS)
def test_scenario_distance_matrix_is_full_symmetric_with_zero_diagonal(api, hdr, sid):
    d = api.get(f"{API}/scenarios/{sid}", headers=hdr).json()
    nodes = [d["depot"]["depot_id"], *[a["area_id"] for a in d["areas"]]]
    m = d["distance_km"]
    assert set(m) == set(nodes)
    for a in nodes:
        assert set(m[a]) == set(nodes), a
        assert m[a][a] == 0.0
        for b in nodes:
            assert m[a][b] == m[b][a]
            assert m[a][b] >= 0.0
            assert m[a][b] == round(m[a][b], 2)
            if a != b:
                assert m[a][b] > 0.0


# --------------------------------------------------------------------------- 6.5 predictions
@pytest.mark.parametrize("sid", SEEDS)
def test_predictions_strict_keys_and_formula_f1(api, hdr, sid):
    sc = api.get(f"{API}/scenarios/{sid}", headers=hdr).json()
    r = api.get(f"{API}/scenarios/{sid}/predictions", headers=hdr)
    assert r.status_code == 200
    d = r.json()
    PredictionSet.model_validate(d)
    assert_prediction_keys(d)
    assert_iso_datetimes(d)
    assert d["schema_version"] == "1.0" and d["scenario_id"] == sid
    assert d["model_version"] == "xgb-sa-v1"
    assert d["is_simulation"] is True
    assert isinstance(d["latency_ms"], int) and d["latency_ms"] >= 0
    assert isinstance(d["run_mae_litres"], float) and d["run_mae_litres"] >= 0.0  # seeds have hidden truth

    by_area = {a["area_id"]: a for a in sc["areas"]}
    assert [a["area_id"] for a in d["areas"]] == [a["area_id"] for a in sc["areas"]]
    for p in d["areas"]:
        area = by_area[p["area_id"]]
        assert AREA_RE.match(p["area_id"])
        assert p["expected_piped_litres"] == area["expected_piped_litres"]
        assert p["predicted_shortfall_litres"] == max(0, p["predicted_need_litres"] - p["expected_piped_litres"])  # F-1
        assert p["baseline_last_need_litres"] == area["history"][-1]["need_litres"]
        feats = p["top_features"]
        assert len(feats) == 3
        names = [f["feature"] for f in feats]
        assert len(set(names)) == 3 and set(names) <= ALLOWED_FEATURES
        mags = [abs(f["contribution_litres"]) for f in feats]
        assert mags == sorted(mags, reverse=True)


def test_predictions_are_stable_across_calls(api, hdr):
    a = api.get(f"{API}/scenarios/scn_sa01_w06/predictions", headers=hdr).json()
    b = api.get(f"{API}/scenarios/scn_sa01_w06/predictions", headers=hdr).json()
    assert a["areas"] == b["areas"] and a["run_mae_litres"] == b["run_mae_litres"]


# --------------------------------------------------------------------------- 6.7 model
def test_model_info_strict_and_disclosed(api, hdr):
    r = api.get(f"{API}/model", headers=hdr)
    assert r.status_code == 200
    d = r.json()
    ModelInfo.model_validate(d)
    assert_model_info_keys(d)
    assert_iso_datetimes(d)
    assert d["schema_version"] == "1.0" and d["model_version"] == "xgb-sa-v1"
    assert d["target"] == "need_litres" and d["is_simulation"] is True
    assert d["train_rows"] > 0 and d["test_rows"] > 0
    for k in ("xgboost", "last_observed_baseline"):
        assert d["metrics"][k]["mae_litres"] > 0 and d["metrics"][k]["rmse_litres"] > 0
    assert d["feature_importances"]
    assert {f["feature"] for f in d["feature_importances"]} <= ALLOWED_FEATURES
    low = d["disclaimer"].lower()
    assert "synthetic" in low and "simulation" in low


# --------------------------------------------------------------------------- 6.6 solve
@pytest.mark.parametrize("sid", SEEDS)
def test_solve_strict_keys_ids_datetimes(api, hdr, sid):
    started = time.perf_counter()
    r = api.post(f"{API}/plans/solve", json=solve_body(sid), headers=hdr)
    elapsed = time.perf_counter() - started
    assert r.status_code == 200, r.text
    assert elapsed <= 2.0 + 1.0  # A must respond within time_limit_s + 1.0 s
    d = r.json()
    PlanResult.model_validate(d)
    assert_plan_keys(d)
    assert_iso_datetimes(d)

    assert d["schema_version"] == "1.0" and d["strategy"] == "jalpriority"
    assert RESULT_RE.match(d["result_id"])
    assert d["scenario_id"] == sid and SERVICE_AREA_RE.match(d["service_area_id"]) and d["window_index"] == 6
    assert d["weights"] == SOLVE_WEIGHTS
    assert d["solver"]["engine"] in {"ortools-cp-sat", "greedy-fallback"}
    assert d["solver"]["status"] in {"optimal", "feasible", "time_limited", "heuristic"}
    assert d["solver"]["time_limit_s"] == 2.0
    assert isinstance(d["solver"]["solve_time_ms"], int)

    pred = api.get(f"{API}/scenarios/{sid}/predictions", headers=hdr).json()
    assert d["prediction"] == {
        "model_version": pred["model_version"],
        "latency_ms": pred["latency_ms"],
        "run_mae_litres": pred["run_mae_litres"],
    }

    assert [a["rank"] for a in d["allocations"]] == list(range(1, len(d["allocations"]) + 1))
    for a in d["allocations"]:
        assert AREA_RE.match(a["area_id"]) and a["reason_text"].strip()
        assert a["reasons"] and all(r["code"] in REASON_CODES for r in a["reasons"])
        assert a["is_unserved_remainder"] == (a["unmet_litres"] > 0)
    assert [r["tanker_id"] for r in d["routes"]] == sorted(r["tanker_id"] for r in d["routes"])
    assert [u["area_id"] for u in d["unserved"]] == [a["area_id"] for a in d["allocations"] if a["unmet_litres"] > 0]
    assert all(u["reason_code"] in UNSERVED_REASON_CODES for u in d["unserved"])
    assert d["constraint_check"]["violations"] == []


def test_solve_default_time_limit_and_tolerant_request(api, hdr):
    body = solve_body()
    body.pop("time_limit_s")
    body["unexpected_extra_key"] = "ignored at runtime (CONTRACT 4.9)"
    r = api.post(f"{API}/plans/solve", json=body, headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["solver"]["time_limit_s"] == 8.0


def test_solve_accepts_empty_and_partial_debt_maps(api, hdr):
    for debt in ({}, {"ar_01": 0}, {"ar_08": 5000}):
        r = api.post(f"{API}/plans/solve", json=solve_body(debt_by_area=debt), headers=hdr)
        assert r.status_code == 200, r.text
        d = r.json()
        for a in d["allocations"]:
            assert a["debt_litres_used"] == debt.get(a["area_id"], 0)


def test_solve_is_deterministic_apart_from_volatile_fields(api, hdr):
    def stable(d):
        d = {k: v for k, v in d.items() if k not in {"result_id", "generated_at"}}
        d["solver"] = {k: v for k, v in d["solver"].items() if k != "solve_time_ms"}
        return d

    a = api.post(f"{API}/plans/solve", json=solve_body(), headers=hdr).json()
    b = api.post(f"{API}/plans/solve", json=solve_body(), headers=hdr).json()
    assert a["solver"]["status"] == "optimal" and b["solver"]["status"] == "optimal"
    assert a["result_id"] != b["result_id"]
    assert stable(a) == stable(b)


# --------------------------------------------------------------------------- 401 / 403
@pytest.mark.parametrize("method,path,body", PROTECTED, ids=[f"{m} {p.removeprefix(API)}" for m, p, _ in PROTECTED])
def test_missing_token_is_401(api, method, path, body):
    err = error_envelope_ok(call(api, method, path, body), 401, "UNAUTHORIZED")
    assert "token" not in err["details"]


@pytest.mark.parametrize("method,path,body", PROTECTED, ids=[f"{m} {p.removeprefix(API)}" for m, p, _ in PROTECTED])
def test_every_protected_route_rejects_a_wrong_issuer(api, make_token, method, path, body):
    r = call(api, method, path, body, bearer(make_token(iss="not-jalpriority-b")))
    error_envelope_ok(r, 401, "UNAUTHORIZED")


@pytest.mark.parametrize("method,path,body", PROTECTED, ids=[f"{m} {p.removeprefix(API)}" for m, p, _ in PROTECTED])
def test_every_protected_route_rejects_guest_role(api, make_token, method, path, body):
    r = call(api, method, path, body, bearer(make_token(role="guest")))
    error_envelope_ok(r, 403, "FORBIDDEN")


def _bad_headers(make_token) -> dict[str, dict[str, str]]:
    now = int(time.time())
    claims = {"iss": "jalpriority-b", "aud": "jalpriority", "sub": "officer_01", "role": "dispatcher", "iat": now, "exp": now + 3600}
    return {
        "wrong_iss": bearer(make_token(iss="someone-else")),
        "wrong_aud": bearer(make_token(aud="other-audience")),
        "wrong_secret": bearer(make_token(_secret="x" * 48)),
        "expired": bearer(make_token(ttl=-3600)),
        "no_exp": bearer(make_token(exp=None)),
        "garbage": bearer("not.a.jwt"),
        "empty_bearer": {"Authorization": "Bearer "},
        "basic_scheme": {"Authorization": "Basic dXNlcjpwYXNz"},
        "raw_token_no_scheme": {"Authorization": make_token()},
        "alg_none": bearer(jwt.encode(claims, key=None, algorithm="none")),
    }


@pytest.mark.parametrize(
    "case",
    ["wrong_iss", "wrong_aud", "wrong_secret", "expired", "no_exp", "garbage", "empty_bearer", "basic_scheme", "raw_token_no_scheme", "alg_none"],
)
def test_bad_credentials_are_401(api, make_token, case):
    headers = _bad_headers(make_token)[case]
    r = api.get(f"{API}/scenarios", headers=headers)
    err = error_envelope_ok(r, 401, "UNAUTHORIZED")
    assert err["message"]


def test_thirty_second_clock_skew_leeway(api, make_token):
    ok = api.get(f"{API}/scenarios", headers=bearer(make_token(ttl=-10)))  # expired 10 s ago, within the 30 s leeway
    assert ok.status_code == 200
    late = api.get(f"{API}/scenarios", headers=bearer(make_token(ttl=-120)))
    error_envelope_ok(late, 401, "UNAUTHORIZED")


@pytest.mark.parametrize("role", ["dispatcher", "admin", "service"])
def test_allowed_roles_are_accepted(api, make_token, role):
    r = api.get(f"{API}/scenarios", headers=bearer(make_token(role=role, sub="svc-b" if role == "service" else "officer_01")))
    assert r.status_code == 200


@pytest.mark.parametrize("role", ["guest", "viewer", "root", ""])
def test_other_roles_are_403(api, make_token, role):
    error_envelope_ok(api.get(f"{API}/scenarios", headers=bearer(make_token(role=role))), 403, "FORBIDDEN")


def test_auth_is_checked_before_not_found_and_validation(api):
    error_envelope_ok(api.get(f"{API}/scenarios/scn_sa01_w99"), 401, "UNAUTHORIZED")
    error_envelope_ok(api.post(f"{API}/plans/solve", json={"nonsense": True}), 401, "UNAUTHORIZED")
    error_envelope_ok(api.post(f"{API}/scenarios/scn_sa01_w99/disruptions", json={}), 401, "UNAUTHORIZED")


# --------------------------------------------------------------------------- 404
@pytest.mark.parametrize(
    "method,path_tpl,body",
    [
        ("GET", "/scenarios/{sid}", None),
        ("GET", "/scenarios/{sid}/predictions", None),
        ("POST", "/scenarios/{sid}/disruptions", {"type": "supply_cut", "params": {"fraction": 0.3}}),
        ("POST", "/plans/solve", "SOLVE"),
    ],
)
@pytest.mark.parametrize("sid", ["scn_sa01_w99", "scn_nothing_here", "scn_sa01_w06__d1_abcdef"])
def test_unknown_scenario_is_404(api, hdr, method, path_tpl, body, sid):
    if body == "SOLVE":
        body = solve_body(sid)
    r = call(api, method, f"{API}{path_tpl.format(sid=sid)}", body, hdr)
    err = error_envelope_ok(r, 404, "SCENARIO_NOT_FOUND")
    assert err["details"] == {"scenario_id": sid}
    assert sid in err["message"]


@pytest.mark.parametrize("sid", ["scn_A", "scn_sa01__w06", "other_thing", "scn_sa01_w06__d1_ZZZZZZ"])
def test_malformed_scenario_ids_are_404_not_500(api, hdr, sid):
    error_envelope_ok(api.get(f"{API}/scenarios/{sid}", headers=hdr), 404, "SCENARIO_NOT_FOUND")


# --------------------------------------------------------------------------- 422 (solve request)
def _errors(err: dict) -> list[dict]:
    errors = err["details"]["errors"]
    assert errors and all(set(e) == {"loc", "msg"} for e in errors), errors
    assert all(isinstance(e["loc"], list) and isinstance(e["msg"], str) and e["msg"] for e in errors)
    return errors


@pytest.mark.parametrize(
    "weights,field",
    [
        ({"urgency": 5.1, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}, "urgency"),
        ({"urgency": 1.0, "debt": -0.1, "vulnerability": 0.5, "travel": 20.0}, "debt"),
        ({"urgency": 1.0, "debt": 1.0, "vulnerability": 6, "travel": 20.0}, "vulnerability"),
        ({"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 500.5}, "travel"),
        ({"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": -1}, "travel"),
        ({"urgency": "high", "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}, "urgency"),
    ],
)
def test_bad_weights_are_422_with_loc_and_msg(api, hdr, weights, field):
    r = api.post(f"{API}/plans/solve", json=solve_body(weights=weights), headers=hdr)
    err = error_envelope_ok(r, 422, "VALIDATION_ERROR")
    assert any("weights" in e["loc"] and field in e["loc"] for e in _errors(err)), err


def test_weight_range_edges_are_accepted(api, hdr):
    for weights in (
        {"urgency": 0, "debt": 0, "vulnerability": 0, "travel": 0},
        {"urgency": 5, "debt": 5, "vulnerability": 5, "travel": 500},
    ):
        r = api.post(f"{API}/plans/solve", json=solve_body(weights=weights), headers=hdr)
        assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "mutate,field",
    [
        (lambda b: b.pop("weights"), "weights"),
        (lambda b: b.pop("debt_by_area"), "debt_by_area"),
        (lambda b: b.pop("scenario_id"), "scenario_id"),
        (lambda b: b.update(time_limit_s=0.4), "time_limit_s"),
        (lambda b: b.update(time_limit_s=9.6), "time_limit_s"),
        (lambda b: b.update(time_limit_s="soon"), "time_limit_s"),
        (lambda b: b.update(debt_by_area={"ar_01": -5}), "debt_by_area"),
        (lambda b: b.update(debt_by_area={"ar_01": 12.5}), "debt_by_area"),
        (lambda b: b.update(debt_by_area={"area_one": 5}), "debt_by_area"),
        (lambda b: b.update(debt_by_area=[1, 2, 3]), "debt_by_area"),
    ],
)
def test_malformed_solve_requests_are_422(api, hdr, mutate, field):
    body = solve_body()
    mutate(body)
    err = error_envelope_ok(api.post(f"{API}/plans/solve", json=body, headers=hdr), 422, "VALIDATION_ERROR")
    assert any(field in e["loc"] for e in _errors(err)), err


def test_non_json_solve_body_is_422(api, hdr):
    r = api.post(f"{API}/plans/solve", content=b"{not json", headers={**hdr, "content-type": "application/json"})
    error_envelope_ok(r, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("unknown", [{"ar_99": 100}, {"ar_01": 0, "ar_09": 5000}, {"ar_00": 1}])
def test_unknown_debt_area_id_is_422(api, hdr, unknown):
    r = api.post(f"{API}/plans/solve", json=solve_body(debt_by_area=unknown), headers=hdr)
    err = error_envelope_ok(r, 422, "VALIDATION_ERROR")
    assert set(err["details"]["unknown_area_ids"]) <= set(unknown) and err["details"]["unknown_area_ids"]


# --------------------------------------------------------------------------- 409 / 422 (disruptions)
def post_disruption(api, hdr, dtype, params, sid="scn_sa01_w06"):
    return api.post(f"{API}/scenarios/{sid}/disruptions", json={"type": dtype, "params": params}, headers=hdr)


@pytest.mark.parametrize(
    "dtype,params",
    [
        ("tanker_down", {"tanker_id": "tk_99"}),
        ("tanker_down", {"tanker_id": "TK_01"}),
        ("tanker_down", {}),
        ("tanker_down", {"tanker_id": 1}),
        ("supply_cut", {"fraction": 0.04}),
        ("supply_cut", {"fraction": 0.95}),
        ("supply_cut", {"fraction": 0}),
        ("supply_cut", {"fraction": "0.3"}),
        ("supply_cut", {}),
        ("heat_alert", {"alert_level": 4}),
        ("heat_alert", {"alert_level": -1}),
        ("heat_alert", {"alert_level": 1.5}),
        ("heat_alert", {"alert_level": "3"}),
        ("heat_alert", {}),
        ("earthquake", {"magnitude": 9}),
    ],
)
def test_invalid_disruptions_are_422(api, hdr, dtype, params):
    err = error_envelope_ok(post_disruption(api, hdr, dtype, params), 422, "DISRUPTION_INVALID")
    assert err["message"]


def test_disruption_range_edges_are_valid(api, hdr):
    for fraction in (0.05, 0.9):
        r = post_disruption(api, hdr, "supply_cut", {"fraction": fraction})
        assert r.status_code in (200, 201), r.text


def test_disruption_body_without_type_is_validation_error(api, hdr):
    r = api.post(f"{API}/scenarios/scn_sa01_w06/disruptions", json={"params": {}}, headers=hdr)
    error_envelope_ok(r, 422, "VALIDATION_ERROR")


def test_tanker_already_unavailable_is_409(api, hdr):
    first = post_disruption(api, hdr, "tanker_down", {"tanker_id": "tk_03"})
    assert first.status_code in (200, 201)
    derived_id = first.json()["scenario_id"]
    again = post_disruption(api, hdr, "tanker_down", {"tanker_id": "tk_03"}, sid=derived_id)
    err = error_envelope_ok(again, 409, "DISRUPTION_NOT_APPLICABLE")
    assert err["details"] == {"tanker_id": "tk_03"}


@pytest.mark.parametrize("sid,level", [("scn_sa01_w06", 2), ("scn_sa01_w06_severe", 3), ("scn_sa01_w06_mild", 1)])
def test_unchanged_heat_level_is_409(api, hdr, sid, level):
    err = error_envelope_ok(post_disruption(api, hdr, "heat_alert", {"alert_level": level}, sid=sid), 409, "DISRUPTION_NOT_APPLICABLE")
    assert err["details"] == {"alert_level": level}


# --------------------------------------------------------------------------- 503 MODEL_UNAVAILABLE
@pytest.fixture(scope="module")
def no_model_api(settings, tmp_path_factory):
    data = copy_real_data(tmp_path_factory.mktemp("contract_nomodel_data"))
    empty_models = tmp_path_factory.mktemp("contract_empty_models")
    with TestClient(create_app(real_settings(settings, data, empty_models))) as c:
        yield c


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", f"{API}/scenarios/scn_sa01_w06/predictions", None),
        ("POST", f"{API}/plans/solve", solve_body()),
        ("GET", f"{API}/model", None),
    ],
)
def test_missing_model_artefact_is_503(no_model_api, hdr, method, path, body):
    err = error_envelope_ok(call(no_model_api, method, path, body, hdr), 503, "MODEL_UNAVAILABLE")
    assert err["message"]


def test_routes_without_a_model_keep_working(no_model_api, hdr):
    assert no_model_api.get(f"{API}/health").status_code == 200
    assert no_model_api.get(f"{API}/scenarios", headers=hdr).status_code == 200
    assert no_model_api.get(f"{API}/scenarios/scn_sa01_w06", headers=hdr).status_code == 200
    # an unknown scenario is still a 404, not a 503
    error_envelope_ok(no_model_api.get(f"{API}/scenarios/scn_sa01_w99/predictions", headers=hdr), 404, "SCENARIO_NOT_FOUND")


def test_503_beats_nothing_for_unauthenticated_calls(no_model_api):
    error_envelope_ok(no_model_api.get(f"{API}/model"), 401, "UNAUTHORIZED")


# --------------------------------------------------------------------------- X-Request-ID
def test_request_id_is_generated_in_the_contract_format(api, hdr):
    ids = set()
    for _ in range(5):
        r = api.get(f"{API}/scenarios", headers=hdr)
        rid = r.headers["x-request-id"]
        assert REQUEST_RE.match(rid)
        ids.add(rid)
    assert len(ids) == 5
    assert REQUEST_RE.match(api.get(f"{API}/health").headers["x-request-id"])


@pytest.mark.parametrize("rid", ["req_0123456789ab", "trace-from-the-gateway-42"])
def test_incoming_request_id_is_reused_on_success(api, hdr, rid):
    assert api.get(f"{API}/scenarios", headers={**hdr, "X-Request-ID": rid}).headers["x-request-id"] == rid
    assert api.get(f"{API}/health", headers={"X-Request-ID": rid}).headers["x-request-id"] == rid
    solved = api.post(f"{API}/plans/solve", json=solve_body(), headers={**hdr, "X-Request-ID": rid})
    assert solved.status_code == 200 and solved.headers["x-request-id"] == rid


@pytest.mark.parametrize("rid", ["req_feedfeedfeed", "trace-from-the-gateway-42"])
def test_incoming_request_id_is_echoed_in_every_error_envelope(api, hdr, no_model_api, rid):
    cases = [
        (api, api.get(f"{API}/scenarios", headers={"X-Request-ID": rid}), 401),
        (api, api.get(f"{API}/scenarios/scn_sa01_w99", headers={**hdr, "X-Request-ID": rid}), 404),
        (api, api.post(f"{API}/plans/solve", json={"x": 1}, headers={**hdr, "X-Request-ID": rid}), 422),
        (api, api.get(f"{API}/model", headers={"Authorization": hdr["Authorization"].replace("Bearer", "Bearer x"), "X-Request-ID": rid}), 401),
        (no_model_api, no_model_api.get(f"{API}/model", headers={**hdr, "X-Request-ID": rid}), 503),
    ]
    for _, response, status in cases:
        assert response.status_code == status
        assert response.headers["x-request-id"] == rid
        assert response.json()["error"]["request_id"] == rid


def test_generated_request_id_matches_between_header_and_error_body(api, hdr):
    r = api.get(f"{API}/scenarios/scn_sa01_w99", headers=hdr)
    err = error_envelope_ok(r, 404, "SCENARIO_NOT_FOUND")
    assert REQUEST_RE.match(err["request_id"])
