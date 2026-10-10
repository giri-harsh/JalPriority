# CONTRACT.md — JalPriority AI (contract v1.0.0, FROZEN)

> **Status: FROZEN.** Read-only for Member A and Member B for the whole build.
> Location in repo: `/contracts/CONTRACT.md`. Nobody edits `/contracts/**` until merge.
> If something here looks wrong, log it in `/member-a/CONTRACT_ISSUES.md` or `/member-b/CONTRACT_ISSUES.md`, keep building against the contract as written, and resolve it at merge.

---

## 0. Assumptions (decisions made where the PRD is ambiguous)

| # | PRD gap | Decision |
|---|---|---|
| A1 | One backend vs. several | **Two FastAPI services**: `jp-a-api` (engine: scenarios, prediction, optimization, explanations) and `jp-b-api` (orchestration: plans, ledger, baselines, metrics, replan, approval, auth, events, audit). The PRD's 3-tier layout (api → services → domain → repositories) is reproduced **inside** each service. |
| A2 | Dashboard framework not specified | React 18 + Vite + TypeScript. **One SPA.** Member B owns the shell. Member A's two screens are mounted into it through a module alias (§9). |
| A3 | Map library | Leaflet + OpenStreetMap tiles, using only `CircleMarker` and `Polyline` (no default marker icons). Automatic fallback to a schematic SVG node-link map when tiles fail. |
| A4 | What the ML model predicts | XGBoost predicts **`predicted_need_litres`** for an area for the coming 12-hour window. **Shortfall** (the demand tankers must cover) = `max(0, predicted_need − expected_piped)`. The model baseline is "last observed need". |
| A5 | Tanker trip model | Each available tanker makes **one multi-stop trip per window**: it starts and ends at the depot, with load ≤ capacity and route km ≤ `max_route_km`. |
| A6 | Priority formula (PRD shows `p = 1 + βD + γV`; US-7 also names "urgency") | `p_i = 1 + α·U_i + β·D̂_i + γ·V_i`. `U` = shortfall / need, and `D̂` = debt normalised by the max debt in the scenario (§5.11). Weights are `{urgency: α, debt: β, vulnerability: γ, travel: δ}`. |
| A7 | Debt update and repayment | `D' = (1 − λ)·D + u`. The decay λ is a municipal policy parameter, default **0.3**, so debt is "repaid" over windows once an area is served. There is an optional cap. Owned by B (§5.11). |
| A8 | Meaning of "edit" a plan | Following the PRD journey ("an edit … returns to the fairness weights"), **edit = adjust weights and regenerate**. B records decision `edited` and creates a superseding plan. Manual litre-by-litre edits are out of scope for v0.1. |
| A9 | Yardstick for comparison | All three strategies are scored against the same `predicted_shortfall_litres` from one PredictionSet. Baselines deliver **requested** litres (what loud or early areas asked for). JalPriority delivers against **predicted shortfall**. |
| A10 | Where data lives | Scenario snapshots, training data and model artefacts belong to A (S3 or local files). Plans, ledger, events, audit and weights belong to B (DynamoDB or SQLite). The PRD's "scenario history in DynamoDB" is B's events table. |
| A11 | Auth | B issues HS256 JWTs (demo users from env). A verifies them with a shared secret. B calls A with a short-lived service token. |
| A12 | AWS | Stretch goal. The default "final setup" is local Docker Compose. Each member deploys only their own AWS stack, behind flags. |
| A13 | Demo scale | One service area `sa_01`, 8 neighbourhoods, 4 tankers, history for windows 1–5, current window 6. |
| A14 | Explanations | Template text built from structured reason codes (§5.10). No LLM, no second model. |
| A15 | Feature flags (PRD §9) | Live in B only. When vulnerability is disabled, B sends `weights.vulnerability = 0` to A, so A never reads flags. |
| A16 | Dependency direction | **B → A only.** A's backend never calls B. A's screens receive B data only through props. |

---

## 1. Ownership summary

| Owner | Slice | PRD coverage |
|---|---|---|
| **A** | **"Need → Plan"**: what is happening, who needs water, and the optimal plan with reasons | US-1–US-5 · F1, F2, F3, F4, F6 · Screens 1 (Crisis snapshot) and 2 (Dispatch plan) · S3 and SageMaker |
| **B** | **"Fairness, Evidence & Control"**: debt, policy, proof, disruption, human decision | US-6–US-10 · F5, F7, F8, F10, F9 shell · Screens 3 (Fairness & debt), 4 (Compare), 5 (Replan) · auth, audit, events · DynamoDB, Lambda, API Gateway |

---

## 2. Repo layout and top-level ownership

```text
jalpriority/
├── contracts/            # FROZEN. Only CONTRACT.md. Nobody edits during the build.
│   └── CONTRACT.md
├── member-a/             # Owner A. Everything below is A's.
├── member-b/             # Owner B. Everything below is B's.
└── integration/          # Touched ONLY at merge time, by both humans together.
    └── INTEGRATION.md    # Present from T0, read-only until merge.
```

Rules:
- At T0 the repo contains only `/contracts/CONTRACT.md`, `/member-a/MEMBER_A.md`, `/member-b/MEMBER_B.md` and `/integration/INTEGRATION.md`. That commit is tagged `t0`.
- Branches: A works on `a/main` and B works on `b/main`, both from `t0`. Neither branch may contain changes outside its own folder.
- There are **no root-level files** during the build: no root `.gitignore`, `package.json`, `pyproject.toml` or `.env`. Each member keeps their own inside their folder.
- Each member runs Claude Code with the working directory set to their own folder (`member-a/` or `member-b/`). Their `CLAUDE.md` lives there.

---

## 3. Ports, base URLs and service names

| Thing | Owner | Local dev | Docker Compose (final) |
|---|---|---|---|
| A API (FastAPI) | A | `http://localhost:8001`, routes under `/a/v1` | service `jp-a-api`, port 8001 |
| B API (FastAPI) | B | `http://localhost:8002`, routes under `/b/v1` | service `jp-b-api`, port 8002 |
| Dashboard shell (Vite dev) | B | `http://localhost:5173` (proxies `/a` → 8001, `/b` → 8002) | built static files served by `jp-gateway` |
| A screen harness (Vite dev) | A | `http://localhost:5174` (proxies `/a` → 8001) | not deployed |
| Gateway (nginx) | integration | n/a | `jp-gateway`, `http://localhost:8080`: `/a/` → jp-a-api, `/b/` → jp-b-api, `/` → SPA |

- Browser code always uses **relative** bases `/a/v1` and `/b/v1`. No absolute URLs in frontend code.
- Server-to-server: B reaches A at `B_UPSTREAM_A_URL`. The default in dev is `http://localhost:8001` and in Compose it is `http://jp-a-api:8001`.
- AWS (stretch): stack `jalpriority-a` (A) and stack `jalpriority-b` (B).

---

## 4. Shared conventions

### 4.1 Naming
- JSON fields: `snake_case`, everywhere, both directions. The frontend keeps data objects in `snake_case` with no case transformation. Only React **props** are `camelCase`.
- Python packages: A = `jp_a`, B = `jp_b`. npm packages: A = `@jp/a-dashboard`, B = `@jp/b-dashboard`.
- CSS: A's class names use the `jpa-` prefix or CSS Modules. B's use `jpb-` or CSS Modules. Shared design tokens are CSS variables with the `--jp-` prefix (§9.6).
- Browser storage keys: A = `jp.a.*` (harness only), B = `jp.b.*`.

### 4.2 IDs

| Entity | Format | Example |
|---|---|---|
| service area | `^sa_\d{2}$` | `sa_01` |
| area | `^ar_\d{2}$` | `ar_03` |
| tanker | `^tk_\d{2}$` | `tk_01` |
| depot | `^dp_\d{2}$` | `dp_01` |
| scenario (seed) | `^scn_[a-z0-9_]+$`, with no `__` | `scn_sa01_w06` |
| scenario (derived) | `^scn_[a-z0-9_]+__d\d+_[0-9a-f]{6}$` | `scn_sa01_w06__d1_3f9a2c` |
| plan result (A or B) | `^res_[0-9a-f]{12}$` | `res_7c1e9a40b2d3` |
| plan record (B) | `^pln_[0-9a-f]{12}$` | `pln_4b8f2a91c0de` |
| event (B) | `^evt_[0-9a-f]{12}$` | `evt_0a1b2c3d4e5f` |
| audit entry (B) | `^aud_[0-9a-f]{12}$` | `aud_9f8e7d6c5b4a` |
| request id | `^req_[0-9a-f]{12}$` | `req_51c0ffee0001` |
| officer | `^(officer|admin)_\d{2}$` | `officer_01` |

### 4.3 Time
ISO 8601, UTC, millisecond precision, `Z` suffix: `2026-10-10T09:15:02.481Z`. Never local time on the wire.

### 4.4 Units and rounding
- Litres: integers. km: float rounded to 2 dp. Ratios, priority weights, urgency, `D̂` and `V`: float rounded to 4 dp. Durations: integer milliseconds (`*_ms`) or float seconds (`*_s`).
- Monetary values: none.

### 4.5 Error envelope (all non-2xx responses from both services)
```json
{
  "error": {
    "code": "SCENARIO_NOT_FOUND",
    "message": "Scenario scn_sa01_w99 does not exist.",
    "details": {"scenario_id": "scn_sa01_w99"},
    "request_id": "req_51c0ffee0001"
  }
}
```

| Code | HTTP | Raised by |
|---|---|---|
| `VALIDATION_ERROR` | 422 | A, B (FastAPI validation errors must be re-wrapped into this envelope; `details.errors` = list of `{loc, msg}`) |
| `UNAUTHORIZED` | 401 | A, B (missing, invalid or expired token) |
| `FORBIDDEN` | 403 | A, B (role not allowed) |
| `SCENARIO_NOT_FOUND` | 404 | A (B passes it through unchanged) |
| `DISRUPTION_INVALID` | 422 | A (unknown tanker id, bad fraction, bad level) |
| `DISRUPTION_NOT_APPLICABLE` | 409 | A (tanker already unavailable, heat level unchanged) |
| `SOLVER_ERROR` | 500 | A (solver crashed; never returned for time limits) |
| `MODEL_UNAVAILABLE` | 503 | A |
| `INVALID_CREDENTIALS` | 401 | B |
| `PLAN_NOT_FOUND` | 404 | B |
| `PLAN_ALREADY_DECIDED` | 409 | B |
| `FEATURE_DISABLED` | 403 | B |
| `UPSTREAM_A_UNAVAILABLE` | 502 | B (A unreachable or timed out) |
| `UPSTREAM_A_ERROR` | 502 | B (A returned an error; `details.upstream` = A's error object). Exception: `SCENARIO_NOT_FOUND` is passed through as 404. |
| `INTERNAL_ERROR` | 500 | A, B |

### 4.6 Logging
One JSON object per line on stdout:
```json
{"ts":"2026-10-10T09:15:02.481Z","level":"INFO","service":"jp-a","request_id":"req_51c0ffee0001","event":"plan_solved","msg":"solved scn_sa01_w06","duration_ms":412}
```
`service` is `jp-a` or `jp-b`. Levels: `DEBUG|INFO|WARNING|ERROR`. Never log tokens or passwords.

### 4.7 Request correlation
- Header `X-Request-ID`. If it is present on an incoming request, reuse it. If not, generate one in the `req_` format.
- Echo it on every response and in the error envelope.
- B forwards it on every call to A.

### 4.8 Environment variables
- Prefixes: `A_*` and `VITE_A_*` are A-owned. `B_*` and `VITE_B_*` are B-owned. `SHARED_*` are contract-owned: both read them, neither defines new ones.
- `SHARED_*` variables are listed here and **set only in `/integration/.env` at merge**. During the build, each member puts dev values for them in their own `.env` (gitignored). They may list them in their own `.env.example` under a `# SHARED (contract-owned, read-only)` heading.

| Variable | Default (dev) | Meaning |
|---|---|---|
| `SHARED_JWT_SECRET` | `dev-only-jalpriority-secret-change-me-0123456789` | HS256 signing secret, at least 32 bytes |
| `SHARED_JWT_ISSUER` | `jalpriority-b` | Required `iss` claim |
| `SHARED_JWT_AUDIENCE` | `jalpriority` | Required `aud` claim |
| `SHARED_ENV` | `local` | `local` \| `docker` \| `aws` |
| `SHARED_LOG_LEVEL` | `INFO` | log level |

Each member's own variables are listed in their spec (MEMBER_A §3, MEMBER_B §3).

### 4.9 Versioning
- URL version `/v1`. Every top-level entity in §5 carries `"schema_version": "1.0"`.
- During the build, no field may be added, renamed or removed in any §5 entity.
- Parsing rule: be **strict in contract tests** (Pydantic `extra="forbid"`) and **tolerant at runtime** (`extra="ignore"`).

### 4.10 Synthetic data disclosure
Every scenario has `"is_synthetic": true`, and every PredictionSet and ModelInfo has `"is_simulation": true`. The shell header always shows a **"SYNTHETIC DATA · SIMULATION RESULTS"** badge. Model metrics are never described as real-world accuracy.

---

## 5. Data models

### 5.1 Entity ownership

| Entity | Owner (produces and stores) | Consumed by |
|---|---|---|
| Weights | B (stored policy); A echoes it | A (input to solve) |
| Scenario, ScenarioSummary | A | B (backend), A screens |
| Disruption (request) | A applies it; B requests it | B |
| PredictionSet | A | B (baselines, events), A screens |
| ModelInfo | A | A screens, B (informational) |
| SolveRequest | B sends it; A defines it | A |
| PlanResult | A (strategy `jalpriority`), B (strategies `fcfs`, `nearest_first`) | B, A screens (inside PlanRecord) |
| Metrics | B | A screens (inside PlanRecord), B screens |
| PlanRecord, Decision | B | A screens (via props) |
| Event | B stores it; A screens emit 2 event types through the `onTrack` prop | B |
| Ledger, Audit, Comparison, Replan | B (internal; not part of this contract) | — |

### 5.2 Weights
```json
{"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
```

| Field | Type | Range | Default | Meaning |
|---|---|---|---|---|
| `urgency` | float | 0–5 | 1.0 | α, multiplies `U_i` |
| `debt` | float | 0–5 | 1.0 | β, multiplies `D̂_i` |
| `vulnerability` | float | 0–5 | 0.5 | γ, multiplies `V_i` |
| `travel` | float | 0–500 | 20.0 | δ, litre-equivalents of unmet need per km of travel |

### 5.3 Scenario
`GET /a/v1/scenarios/{scenario_id}` returns this. Real seed scenarios have 8 areas, 4 tankers and history for windows 1–5. **The example below is abbreviated to 3 areas and 3 tankers** and is self-consistent with §5.4, §5.6 and §5.7.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | `"1.0"` |
| `scenario_id` | string | §4.2 |
| `parent_scenario_id` | string \| null | set on derived (disrupted) scenarios |
| `service_area_id` | string | |
| `service_area_name` | string | |
| `window_index` | int ≥ 1 | the dispatch window this snapshot plans for |
| `window_start` | datetime | |
| `window_hours` | int | 12 |
| `is_synthetic` | bool | always `true` |
| `seed` | int | generator seed |
| `heat` | object | `alert_level` int 0–3, `max_temp_c` float, `heat_index_c` float |
| `supply` | object | `tanker_water_available_litres` int (S) |
| `depot` | object | `depot_id`, `name`, `lat`, `lon` |
| `tankers[]` | array | `tanker_id`, `capacity_litres` int, `status` = `available` \| `unavailable`, `max_route_km` float |
| `areas[]` | array | see the example; 6–10 entries |
| `areas[].vulnerability_index` | float 0–1 | aggregated, neighbourhood level |
| `areas[].vulnerability_available` | bool | when false, V is treated as 0 |
| `areas[].heat_exposure` | float 0–1 | |
| `areas[].expected_piped_litres` | int | piped supply expected this window |
| `areas[].requested_litres` | int | raw tanker request (used by baselines) |
| `areas[].request_time` | datetime | used by FCFS |
| `areas[].last_delivery_litres` | int | last tanker delivery |
| `areas[].last_delivery_at` | datetime \| null | |
| `areas[].history[]` | array | `window_index`, `need_litres`, `received_litres` (piped + tanker), `max_temp_c`, `alert_level`; windows < `window_index`, ascending |
| `distance_km` | object | full symmetric matrix over the depot and all area ids: `distance_km[from][to]`, with diagonal 0.0 |
| `disruptions_applied[]` | array | `type`, `params`, `applied_at`; empty for seeds |

```json
{
  "schema_version": "1.0",
  "scenario_id": "scn_sa01_w06",
  "parent_scenario_id": null,
  "service_area_id": "sa_01",
  "service_area_name": "Synthetic Service Area 01",
  "window_index": 6,
  "window_start": "2026-05-21T06:00:00.000Z",
  "window_hours": 12,
  "is_synthetic": true,
  "seed": 42,
  "heat": {"alert_level": 2, "max_temp_c": 44.5, "heat_index_c": 49.0},
  "supply": {"tanker_water_available_litres": 20000},
  "depot": {"depot_id": "dp_01", "name": "Central Depot (synthetic)", "lat": 28.6692, "lon": 77.4538},
  "tankers": [
    {"tanker_id": "tk_01", "capacity_litres": 12000, "status": "available", "max_route_km": 60.0},
    {"tanker_id": "tk_02", "capacity_litres": 10000, "status": "available", "max_route_km": 60.0},
    {"tanker_id": "tk_03", "capacity_litres": 10000, "status": "unavailable", "max_route_km": 60.0}
  ],
  "areas": [
    {
      "area_id": "ar_01", "name": "Ward 01 (synthetic)", "lat": 28.6801, "lon": 77.4412,
      "population": 18000, "vulnerability_index": 0.30, "vulnerability_available": true,
      "heat_exposure": 0.55, "expected_piped_litres": 40000,
      "requested_litres": 20000, "request_time": "2026-05-21T05:05:00.000Z",
      "last_delivery_litres": 8000, "last_delivery_at": "2026-05-20T14:10:00.000Z",
      "history": [
        {"window_index": 4, "need_litres": 52000, "received_litres": 52000, "max_temp_c": 42.0, "alert_level": 1},
        {"window_index": 5, "need_litres": 53000, "received_litres": 53000, "max_temp_c": 43.1, "alert_level": 1}
      ]
    },
    {
      "area_id": "ar_02", "name": "Ward 02 (synthetic)", "lat": 28.6915, "lon": 77.4705,
      "population": 11000, "vulnerability_index": 0.55, "vulnerability_available": true,
      "heat_exposure": 0.70, "expected_piped_litres": 24000,
      "requested_litres": 9000, "request_time": "2026-05-21T05:40:00.000Z",
      "last_delivery_litres": 6000, "last_delivery_at": "2026-05-20T15:30:00.000Z",
      "history": [
        {"window_index": 4, "need_litres": 31000, "received_litres": 26000, "max_temp_c": 42.0, "alert_level": 1},
        {"window_index": 5, "need_litres": 32000, "received_litres": 27500, "max_temp_c": 43.1, "alert_level": 1}
      ]
    },
    {
      "area_id": "ar_03", "name": "Ward 03 (synthetic)", "lat": 28.7120, "lon": 77.5010,
      "population": 7000, "vulnerability_index": 0.80, "vulnerability_available": true,
      "heat_exposure": 0.90, "expected_piped_litres": 15000,
      "requested_litres": 4000, "request_time": "2026-05-21T06:55:00.000Z",
      "last_delivery_litres": 0, "last_delivery_at": null,
      "history": [
        {"window_index": 4, "need_litres": 20000, "received_litres": 14000, "max_temp_c": 42.0, "alert_level": 1},
        {"window_index": 5, "need_litres": 20500, "received_litres": 12700, "max_temp_c": 43.1, "alert_level": 1}
      ]
    }
  ],
  "distance_km": {
    "dp_01": {"dp_01": 0.0, "ar_01": 3.1, "ar_02": 6.4, "ar_03": 11.8},
    "ar_01": {"dp_01": 3.1, "ar_01": 0.0, "ar_02": 4.0, "ar_03": 9.5},
    "ar_02": {"dp_01": 6.4, "ar_01": 4.0, "ar_02": 0.0, "ar_03": 6.2},
    "ar_03": {"dp_01": 11.8, "ar_01": 9.5, "ar_02": 6.2, "ar_03": 0.0}
  },
  "disruptions_applied": []
}
```

**ScenarioSummary** (items of `GET /a/v1/scenarios`):
```json
{
  "schema_version": "1.0",
  "scenario_id": "scn_sa01_w06",
  "parent_scenario_id": null,
  "service_area_id": "sa_01",
  "window_index": 6,
  "label": "Main demo · heat alert 2 · supply ≈55% of shortfall",
  "n_areas": 8,
  "supply_litres": 20000,
  "tankers_available": 2,
  "alert_level": 2,
  "is_synthetic": true
}
```

### 5.4 PredictionSet
`GET /a/v1/scenarios/{scenario_id}/predictions`. Values are illustrative except where §5.11 binds them; the `predicted_shortfall_litres` values are the binding inputs for §5.6.

```json
{
  "schema_version": "1.0",
  "scenario_id": "scn_sa01_w06",
  "model_version": "xgb-sa-v1",
  "generated_at": "2026-10-10T09:15:02.050Z",
  "latency_ms": 41,
  "run_mae_litres": 640.0,
  "is_simulation": true,
  "areas": [
    {"area_id": "ar_01", "predicted_need_litres": 54000, "expected_piped_litres": 40000, "predicted_shortfall_litres": 14000, "baseline_last_need_litres": 53000,
     "top_features": [{"feature": "heat_index_c", "contribution_litres": 2300.0}, {"feature": "population", "contribution_litres": 1900.0}, {"feature": "last_need_litres", "contribution_litres": 1200.0}]},
    {"area_id": "ar_02", "predicted_need_litres": 33000, "expected_piped_litres": 24000, "predicted_shortfall_litres": 9000, "baseline_last_need_litres": 32000,
     "top_features": [{"feature": "heat_index_c", "contribution_litres": 1500.0}, {"feature": "heat_exposure", "contribution_litres": 900.0}, {"feature": "mean_need_last3_litres", "contribution_litres": 700.0}]},
    {"area_id": "ar_03", "predicted_need_litres": 21000, "expected_piped_litres": 15000, "predicted_shortfall_litres": 6000, "baseline_last_need_litres": 20500,
     "top_features": [{"feature": "heat_exposure", "contribution_litres": 1100.0}, {"feature": "heat_index_c", "contribution_litres": 950.0}, {"feature": "alert_level", "contribution_litres": 400.0}]}
  ]
}
```
- `run_mae_litres`: MAE of `predicted_need_litres` against the generator's hidden synthetic ground truth for this scenario. It is `null` for derived scenarios.
- `top_features`: exactly 3 entries, sorted by |contribution| descending. Allowed feature names: `max_temp_c`, `heat_index_c`, `alert_level`, `heat_exposure`, `population`, `last_need_litres`, `mean_need_last3_litres`, `last_received_litres`, `expected_piped_litres`.

### 5.5 ModelInfo
`GET /a/v1/model`. Values are illustrative.
```json
{
  "schema_version": "1.0",
  "model_version": "xgb-sa-v1",
  "trained_at": "2026-10-10T08:00:00.000Z",
  "train_rows": 6912,
  "test_rows": 1680,
  "target": "need_litres",
  "metrics": {
    "xgboost": {"mae_litres": 1520.4, "rmse_litres": 2105.9},
    "last_observed_baseline": {"mae_litres": 2890.7, "rmse_litres": 3954.2}
  },
  "feature_importances": [
    {"feature": "heat_index_c", "importance": 0.31},
    {"feature": "last_need_litres", "importance": 0.24},
    {"feature": "population", "importance": 0.17}
  ],
  "is_simulation": true,
  "disclaimer": "Trained and evaluated on synthetic data. Simulation results only; not real-world accuracy."
}
```

### 5.6 PlanResult (shared by all strategies)
A produces it for `strategy = "jalpriority"`. B produces it for `"fcfs"` and `"nearest_first"`.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | `"1.0"` |
| `result_id` | string | `res_…` |
| `strategy` | enum | `jalpriority` \| `fcfs` \| `nearest_first` |
| `scenario_id`, `service_area_id`, `window_index` | | copied from the scenario |
| `generated_at` | datetime | |
| `weights` | Weights | the weights used; baselines echo the comparison weights |
| `solver` | object | `engine` = `ortools-cp-sat` \| `greedy-fallback` \| `baseline-fcfs` \| `baseline-nearest` \| `mock`; `status` = `optimal` \| `feasible` \| `time_limited` \| `heuristic`; `solve_time_ms` int; `time_limit_s` float \| null |
| `prediction` | object | `model_version`, `latency_ms`, `run_mae_litres` (copied from the PredictionSet used) |
| `totals` | object | `supply_available_litres`, `tanker_capacity_available_litres`, `shortfall_litres`, `allocated_litres`, `unmet_litres`, `travel_km` |
| `allocations[]` | array | one per area, sorted by `rank` ascending |
| `allocations[].rank` | int | 1 = first priority (JalPriority: by `priority_weight` desc, then shortfall desc, then area_id asc; baselines: service order, with unserved areas after served ones) |
| `allocations[].predicted_shortfall_litres` | int | `sf_i` |
| `allocations[].allocated_litres` | int | `x_i` = sum of its deliveries |
| `allocations[].unmet_litres` | int | F-6 |
| `allocations[].coverage_ratio` | float | F-7 |
| `allocations[].priority_weight` | float | F-5 |
| `allocations[].urgency` | float | F-2 |
| `allocations[].debt_litres_used` | int | `D_i` (input debt) |
| `allocations[].debt_normalized` | float | F-3 |
| `allocations[].vulnerability_used` | float | F-4 |
| `allocations[].deliveries[]` | array | `tanker_id`, `stop_order`, `litres` |
| `allocations[].reasons[]` | array | at least 1 item, each `{code, params}` (§5.10) |
| `allocations[].reason_text` | string | plain-language sentence or sentences, never empty |
| `allocations[].is_unserved_remainder` | bool | `unmet_litres > 0` |
| `routes[]` | array | one per tanker in the scenario, sorted by `tanker_id` |
| `routes[].status` | enum | `assigned` \| `idle` \| `unavailable` |
| `routes[].stops[]` | array | `stop_order` (1..n), `area_id`, `litres` |
| `routes[].path` | array | node ids; starts and ends with the depot id; `[]` when idle or unavailable |
| `routes[].load_litres`, `capacity_litres`, `max_route_km`, `route_km` | | F-8 |
| `unserved[]` | array | one per area with `unmet_litres > 0`: `area_id`, `unmet_litres`, `reason_code` |
| `constraint_check` | object | `water_cap_ok`, `capacity_ok`, `route_limit_ok`, `unavailable_tanker_ok` (bools), `violations[]` of `{code, detail}` with codes `WATER_CAP_EXCEEDED` \| `TANKER_CAPACITY_EXCEEDED` \| `ROUTE_LIMIT_EXCEEDED` \| `UNAVAILABLE_TANKER_USED` |

Example: JalPriority on §5.3 with default weights and debt `{ar_01: 0, ar_02: 8000, ar_03: 12000}`. These numbers are **binding** for contract tests.

```json
{
  "schema_version": "1.0",
  "result_id": "res_7c1e9a40b2d3",
  "strategy": "jalpriority",
  "scenario_id": "scn_sa01_w06",
  "service_area_id": "sa_01",
  "window_index": 6,
  "generated_at": "2026-10-10T09:15:02.481Z",
  "weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0},
  "solver": {"engine": "ortools-cp-sat", "status": "optimal", "solve_time_ms": 412, "time_limit_s": 8.0},
  "prediction": {"model_version": "xgb-sa-v1", "latency_ms": 41, "run_mae_litres": 640.0},
  "totals": {
    "supply_available_litres": 20000,
    "tanker_capacity_available_litres": 22000,
    "shortfall_litres": 29000,
    "allocated_litres": 20000,
    "unmet_litres": 9000,
    "travel_km": 37.2
  },
  "allocations": [
    {
      "area_id": "ar_03", "rank": 1,
      "predicted_shortfall_litres": 6000, "allocated_litres": 6000, "unmet_litres": 0, "coverage_ratio": 1.0,
      "priority_weight": 2.6857, "urgency": 0.2857, "debt_litres_used": 12000, "debt_normalized": 1.0, "vulnerability_used": 0.8,
      "deliveries": [{"tanker_id": "tk_01", "stop_order": 1, "litres": 6000}],
      "reasons": [
        {"code": "HIGH_DEBT", "params": {"debt_litres": 12000}},
        {"code": "HIGH_VULNERABILITY", "params": {"vulnerability_index": 0.8}},
        {"code": "FULLY_SERVED", "params": {"allocated_litres": 6000}}
      ],
      "reason_text": "Served in full (6,000 L). Highest priority (2.69): largest accumulated deprivation debt (12,000 L) and high vulnerability (0.80).",
      "is_unserved_remainder": false
    },
    {
      "area_id": "ar_02", "rank": 2,
      "predicted_shortfall_litres": 9000, "allocated_litres": 9000, "unmet_litres": 0, "coverage_ratio": 1.0,
      "priority_weight": 2.2144, "urgency": 0.2727, "debt_litres_used": 8000, "debt_normalized": 0.6667, "vulnerability_used": 0.55,
      "deliveries": [{"tanker_id": "tk_02", "stop_order": 1, "litres": 9000}],
      "reasons": [
        {"code": "HIGH_DEBT", "params": {"debt_litres": 8000}},
        {"code": "FULLY_SERVED", "params": {"allocated_litres": 9000}}
      ],
      "reason_text": "Served in full (9,000 L). Priority 2.21, driven by accumulated deprivation debt of 8,000 L from earlier windows.",
      "is_unserved_remainder": false
    },
    {
      "area_id": "ar_01", "rank": 3,
      "predicted_shortfall_litres": 14000, "allocated_litres": 5000, "unmet_litres": 9000, "coverage_ratio": 0.3571,
      "priority_weight": 1.4093, "urgency": 0.2593, "debt_litres_used": 0, "debt_normalized": 0.0, "vulnerability_used": 0.3,
      "deliveries": [{"tanker_id": "tk_01", "stop_order": 2, "litres": 5000}],
      "reasons": [
        {"code": "LOWER_RELATIVE_PRIORITY", "params": {"priority_weight": 1.4093}},
        {"code": "PARTIALLY_SERVED", "params": {"allocated_litres": 5000, "predicted_shortfall_litres": 14000}},
        {"code": "SUPPLY_EXHAUSTED", "params": {"supply_available_litres": 20000, "unmet_litres": 9000}}
      ],
      "reason_text": "Partially served: 5,000 of 14,000 L (36%). 9,000 L remain unserved because all 20,000 L of tanker water went to higher-priority areas first; this area has no prior debt and low vulnerability (priority 1.41).",
      "is_unserved_remainder": true
    }
  ],
  "routes": [
    {"tanker_id": "tk_01", "status": "assigned", "capacity_litres": 12000, "max_route_km": 60.0, "load_litres": 11000, "route_km": 24.4,
     "stops": [{"stop_order": 1, "area_id": "ar_03", "litres": 6000}, {"stop_order": 2, "area_id": "ar_01", "litres": 5000}],
     "path": ["dp_01", "ar_03", "ar_01", "dp_01"]},
    {"tanker_id": "tk_02", "status": "assigned", "capacity_litres": 10000, "max_route_km": 60.0, "load_litres": 9000, "route_km": 12.8,
     "stops": [{"stop_order": 1, "area_id": "ar_02", "litres": 9000}],
     "path": ["dp_01", "ar_02", "dp_01"]},
    {"tanker_id": "tk_03", "status": "unavailable", "capacity_litres": 10000, "max_route_km": 60.0, "load_litres": 0, "route_km": 0.0,
     "stops": [], "path": []}
  ],
  "unserved": [{"area_id": "ar_01", "unmet_litres": 9000, "reason_code": "SUPPLY_EXHAUSTED"}],
  "constraint_check": {"water_cap_ok": true, "capacity_ok": true, "route_limit_ok": true, "unavailable_tanker_ok": true, "violations": []}
}
```

### 5.7 Metrics (computed by B)
The seven PRD metrics. Example: the §5.6 result with debt decay λ = 0.3. **Binding.**
```json
{
  "schema_version": "1.0",
  "priority_weighted_unmet": 12683.7,
  "deprivation_debt_remaining_litres": 23000,
  "total_unmet_litres": 9000,
  "min_coverage_ratio": 0.3571,
  "total_travel_km": 37.2,
  "constraint_violations": 0,
  "replanning_time_ms": null
}
```

### 5.8 PlanRecord (B-owned; rendered by A's DispatchPlanScreen through props)

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | `"1.0"` |
| `plan_id` | string | `pln_…` |
| `status` | enum | `recommended` \| `approved` \| `edited` \| `rejected` \| `superseded` |
| `created_at` | datetime | |
| `created_by` | string | officer id |
| `scenario_id`, `service_area_id`, `window_index` | | |
| `supersedes_plan_id` | string \| null | set when this plan came from an `edited` decision |
| `replan_of_plan_id` | string \| null | set when this plan came from a disruption replan |
| `disruption` | object \| null | `{type, params}` when it is a replan |
| `is_synthetic` | bool | `true` |
| `result` | PlanResult | strategy is always `jalpriority` |
| `metrics` | Metrics | |
| `decision` | object \| null | `decision` = `approved` \| `edited` \| `rejected`; `officer_id`; `note` (string or null); `decided_at`; `next_plan_id` (string or null) |

Status transitions: `recommended → approved | edited | rejected | superseded`. Each plan gets exactly one terminal transition.

```json
{
  "schema_version": "1.0",
  "plan_id": "pln_4b8f2a91c0de",
  "status": "recommended",
  "created_at": "2026-10-10T09:15:02.600Z",
  "created_by": "officer_01",
  "scenario_id": "scn_sa01_w06",
  "service_area_id": "sa_01",
  "window_index": 6,
  "supersedes_plan_id": null,
  "replan_of_plan_id": null,
  "disruption": null,
  "is_synthetic": true,
  "result": {"comment": "In real payloads this key holds the full PlanResult object exactly as in the §5.6 example."},
  "metrics": {"schema_version": "1.0", "priority_weighted_unmet": 12683.7, "deprivation_debt_remaining_litres": 23000, "total_unmet_litres": 9000, "min_coverage_ratio": 0.3571, "total_travel_km": 37.2, "constraint_violations": 0, "replanning_time_ms": null},
  "decision": null
}
```
(The `result` value above is abbreviated for length. Contract fixtures must embed the full §5.6 object.)

### 5.9 Event (stored by B)
```json
{
  "schema_version": "1.0",
  "event_id": "evt_0a1b2c3d4e5f",
  "name": "scenario_loaded",
  "ts": "2026-10-10T09:14:58.120Z",
  "officer_id": "officer_01",
  "scenario_id": "scn_sa01_w06",
  "props": {"n_areas": 8, "supply_litres": 20000, "tankers_available": 2}
}
```
Event names and props are defined in §7.

### 5.10 Reason codes (closed enum)

| Code | Used by | Params |
|---|---|---|
| `HIGH_PREDICTED_SHORTFALL` | A | `predicted_shortfall_litres`, `urgency` |
| `HIGH_DEBT` | A | `debt_litres` |
| `HIGH_VULNERABILITY` | A | `vulnerability_index` |
| `HEAT_ALERT` | A | `alert_level` |
| `LOWER_RELATIVE_PRIORITY` | A | `priority_weight` |
| `FULLY_SERVED` | A, B | `allocated_litres` |
| `PARTIALLY_SERVED` | A, B | `allocated_litres`, `predicted_shortfall_litres` |
| `NOT_SERVED` | A, B | `predicted_shortfall_litres` |
| `NO_SHORTFALL` | A, B | — |
| `SUPPLY_EXHAUSTED` | A, B | `supply_available_litres`, `unmet_litres` |
| `TANKER_CAPACITY_LIMIT` | A, B | `tanker_capacity_available_litres`, `unmet_litres` |
| `ROUTE_LIMIT` | A, B | `distance_from_depot_km` |
| `TRAVEL_COST_TRADEOFF` | A | `distance_from_depot_km`, `travel_weight` |
| `TIME_LIMITED_SOLUTION` | A | `time_limit_s` |
| `FCFS_ORDER` | B | `request_time`, `position` |
| `NEAREST_FIRST_ORDER` | B | `distance_from_depot_km`, `position` |

`unserved[].reason_code` must be one of `SUPPLY_EXHAUSTED`, `TANKER_CAPACITY_LIMIT`, `ROUTE_LIMIT`, `TRAVEL_COST_TRADEOFF`, `LOWER_RELATIVE_PRIORITY`.

### 5.11 Shared formulas (normative; both sides implement them and contract tests check them)
For area `i` in a scenario, with weights `w`, input debt `D_i` (missing = 0), and the PredictionSet values:

- **F-1 Shortfall:** `sf_i = max(0, predicted_need_i − expected_piped_i)` (integer, computed by A).
- **F-2 Urgency:** `U_i = sf_i / predicted_need_i` if `predicted_need_i > 0`, else `0`.
- **F-3 Normalised debt:** `D̂_i = D_i / max(1, max_j D_j)`, with the max taken over all areas in the scenario.
- **F-4 Vulnerability:** `V_i = vulnerability_index_i` if `vulnerability_available_i`, else `0`.
- **F-5 Priority:** `p_i = round(1 + w.urgency·U_i + w.debt·D̂_i + w.vulnerability·V_i, 4)`, computed from unrounded `U`, `D̂` and `V`. Stored fields `urgency`, `debt_normalized` and `vulnerability_used` are rounded to 4 dp.
- **F-6 Unmet:** `u_i = max(0, sf_i − allocated_i)`.
- **F-7 Coverage:** `c_i = 1.0` if `sf_i = 0`, else `round(min(1, allocated_i / sf_i), 4)`.
- **F-8 Route km:** the sum of `distance_km[path[k]][path[k+1]]` along the path, rounded to 2 dp; `0.0` when the path is empty.
- **F-9 Priority-weighted unmet:** `round(Σ_i p_i · u_i, 1)`, using the rounded `p_i`. For baselines, `p_i` is computed with the same F-5 inputs.
- **F-10 Debt update (B):** `D_i' = round((1 − λ)·D_i + u_i)`, then `min(D_i', cap)` if `B_DEBT_CAP_LITRES` is set. λ = `B_DEBT_DECAY` (default 0.3).
- **F-11 Debt remaining:** `Σ_i D_i'`.
- **F-12 Total unmet:** `Σ_i u_i`.
- **F-13 Min coverage:** `min_i c_i` over areas with `sf_i > 0`; `1.0` if there are none.
- **F-14 Travel:** `Σ_k route_km_k`, rounded to 2 dp.
- **F-15 Constraint violations** (count):
  - +1 if `Σ allocated > supply_available_litres`
  - +1 per tanker whose load exceeds its capacity
  - +1 per route with `route_km > max_route_km + 0.01`
  - +1 per delivery made by an `unavailable` tanker

Worked check on the example. With `D = {0, 8000, 12000}` → `D̂ = {0, 0.6667, 1}`:
- `p_01 = 1 + 0.2593 + 0 + 0.15 = 1.4093`
- `p_02 = 1 + 0.2727 + 0.6667 + 0.275 = 2.2144`
- `p_03 = 1 + 0.2857 + 1 + 0.4 = 2.6857`
- PWU = 1.4093 × 9000 = **12683.7**
- Debt remaining = (0.7·0 + 9000) + (0.7·8000 + 0) + (0.7·12000 + 0) = **23000**

Ledger seeding check (B): with λ = 0.3, the history in §5.3 gives debt after window 5 of **ar_01 = 0, ar_02 = 8000, ar_03 = 12000**. For ar_03: 6000 after w4, then 0.7·6000 + 7800 = 12000 after w5.

### 5.12 Disruption semantics (A implements; B's mock must replicate them exactly)
Request body for `POST /a/v1/scenarios/{scenario_id}/disruptions`:

| `type` | `params` | Effect on the derived scenario | Errors |
|---|---|---|---|
| `tanker_down` | `{"tanker_id": "tk_01"}` | that tanker's `status` → `unavailable` | 422 `DISRUPTION_INVALID` if the tanker id is unknown; 409 `DISRUPTION_NOT_APPLICABLE` if it is already unavailable |
| `supply_cut` | `{"fraction": 0.3}` (0.05 ≤ f ≤ 0.9) | `S' = floor(S·(1 − f) / 100) · 100` | 422 if f is out of range |
| `heat_alert` | `{"alert_level": 3}` (integer 0–3) | `Δ = new − old`; `alert_level = new`; `max_temp_c += 1.5·Δ`; `heat_index_c += 2.5·Δ` (round to 1 dp) | 422 if out of range; 409 if `Δ = 0` |

- The derived scenario is a full Scenario. Its `parent_scenario_id` = the scenario the disruption was applied to. `disruptions_applied` = the parent's list plus `{type, params, applied_at}`.
- **Derived id:** `{root}__d{k}_{h}`, where:
  - `root` = the seed id (the part before the first `__`)
  - `k` = `len(disruptions_applied)`
  - `h` = the first 6 hex chars of `sha1(json.dumps([{"type":t,"params":p} for each applied], sort_keys=True, separators=(",",":")))`
- If that id already exists, return the existing scenario (idempotent) with 200 instead of 201.
- Derived scenarios are persisted and retrievable with GET like any other scenario.

---

## 6. A's API (provided by A; consumed by B's backend and by A's own screens)

All routes are under `/a/v1`. JSON only. Auth is described in §8. All errors use §4.5.

| # | Method and path | Auth | Request | 2xx response | Errors |
|---|---|---|---|---|---|
| 6.1 | `GET /a/v1/health` | none | — | 200 `{"status":"ok","service":"jp-a","version":"1.0.0","model_version":"xgb-sa-v1","time":"…"}` | — |
| 6.2 | `GET /a/v1/scenarios?include_derived=false` | Bearer | query `include_derived` bool, default false | 200 `{"scenarios":[ScenarioSummary…]}`, sorted by `scenario_id` | 401 |
| 6.3 | `GET /a/v1/scenarios/{scenario_id}` | Bearer | — | 200 Scenario (§5.3) | 401, 404 `SCENARIO_NOT_FOUND` |
| 6.4 | `POST /a/v1/scenarios/{scenario_id}/disruptions` | Bearer | `{"type": "...", "params": {...}}` (§5.12) | 201 Scenario (derived), or 200 if it already exists | 401, 404, 409, 422 |
| 6.5 | `GET /a/v1/scenarios/{scenario_id}/predictions` | Bearer | — | 200 PredictionSet (§5.4) | 401, 404, 503 `MODEL_UNAVAILABLE` |
| 6.6 | `POST /a/v1/plans/solve` | Bearer | SolveRequest (below) | 200 PlanResult (§5.6, strategy `jalpriority`) | 401, 404, 422, 500 `SOLVER_ERROR`, 503 |
| 6.7 | `GET /a/v1/model` | Bearer | — | 200 ModelInfo (§5.5) | 401, 503 |

**SolveRequest** (6.6):
```json
{
  "scenario_id": "scn_sa01_w06",
  "weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0},
  "debt_by_area": {"ar_01": 0, "ar_02": 8000, "ar_03": 12000},
  "time_limit_s": 8.0
}
```
- `weights` is required (§5.2 ranges).
- `debt_by_area` is required. It may omit areas (treated as 0). Unknown area ids → 422. Values are integers ≥ 0.
- `time_limit_s` is optional, default 8.0, range [0.5, 9.5]. A must respond within `time_limit_s + 1.0` s.
- Behaviour guarantees:
  - A always returns a PlanResult with `constraint_check.violations == []`, never an error for a time limit. When the limit is hit, A returns the best feasible plan with `status: "time_limited"`, or `status: "heuristic"` with engine `greedy-fallback`.
  - A prediction is computed or cached internally for the same scenario, and the `prediction` block is filled from it.
  - For identical inputs with no time-limit hit, results are identical apart from `result_id`, `generated_at` and timings.

---

## 7. Events and messages between the two sides
- There are **no queues, websockets or webhooks** between A and B. All integration is synchronous HTTP (§6) or React props (§9).
- **A → B event path:** A's screens emit exactly 2 event types through the `onTrack(event)` prop. B's shell adds `event_id`, `ts` and `officer_id` and persists them through B's events store. A never sends events anywhere else.
- **Event catalogue.** Each event has exactly one emitter. Its `props` keys are exact.

| Event | Emitter | When | `props` |
|---|---|---|---|
| `scenario_loaded` | **A screen** (`onTrack`) | SnapshotScreen finishes loading a scenario (once per scenario id per mount) | `n_areas`, `supply_litres`, `tankers_available` |
| `explanation_viewed` | **A screen** (`onTrack`) | a user expands an area's reason in DispatchPlanScreen | `plan_id`, `area_id` |
| `prediction_generated` | B | after each plan creation | `model_version`, `run_mae_litres`, `latency_ms` |
| `plan_generated` | B | after each JalPriority plan or baseline result | `plan_id` (null for baselines), `strategy`, `weights`, `solve_time_ms`, `solver_status`, `feasible` |
| `weights_changed` | B | weights saved | `old_weights`, `new_weights` |
| `disruption_applied` | B | replan requested | `type`, `magnitude`, `params`, `derived_scenario_id` |
| `replan_completed` | B | replan returned | `previous_plan_id`, `new_plan_id`, `replanning_time_ms`, `diff_summary` (`areas_changed`, `litres_moved`) |
| `comparison_viewed` | B | Compare screen shows a result | `metrics_by_strategy` |
| `plan_decision` | B | approve, edit or reject | `plan_id`, `decision`, `officer_id` |

`magnitude`: `tanker_down` → 1 (tankers lost), `supply_cut` → `fraction`, `heat_alert` → new `alert_level`.

---

## 8. Auth and session

| Concern | Owner | Rule |
|---|---|---|
| Login, token issuing, demo users | **B** | `POST /b/v1/auth/login` (B-internal) returns a JWT |
| Token format | contract | JWT HS256 signed with `SHARED_JWT_SECRET`. Claims: `iss` = `SHARED_JWT_ISSUER` (`jalpriority-b`), `aud` = `SHARED_JWT_AUDIENCE` (`jalpriority`), `sub` (officer id or `svc-b`), `role` ∈ {`dispatcher`, `admin`, `service`}, `iat`, `exp` (users 8 h, service 5 min). 30 s clock-skew leeway. |
| Token verification on `/a/v1/*` | **A** | Every route except health requires `Authorization: Bearer <jwt>`. Bad, missing or expired token → 401 `UNAUTHORIZED`. Wrong iss or aud → 401. Role outside the allowed set → 403. `A_AUTH_DISABLED=true` skips checks; it is for A's local tests only and must be `false` at integration. |
| B → A service calls | **B** | B mints `{"sub":"svc-b","role":"service"}` tokens and caches them for 4 minutes. |
| Browser session | **B** | The token is held by the shell (memory + `sessionStorage["jp.b.token"]`) and passed to A's screens as the `token` prop. A's screens never read storage and never call `/b/*`. |
| Audit of approvals and edits | **B** | |

Dev token for A's isolated testing: A's own script mints a token with exactly the claims above, using `SHARED_JWT_SECRET`.

---

## 9. Shared UI shell

### 9.1 Ownership
- The shell is owned by **B**, at `/member-b/dashboard`. It includes the app root, router, login, header, tab bar, global state, design tokens, the approval bar and the API calls to `/b/v1`.
- A owns the module at `/member-a/dashboard/src/screens/index.ts` and everything it imports.

### 9.2 Mount mechanism
- B's code imports A's screens **only** from the module specifier `@jp/a-screens`.
- B's `vite.config.ts` resolves that alias as follows:
  - `VITE_B_USE_MOCK_A_SCREENS=true` (dev default) → B's own mock `src/mocks/aScreens.tsx`
  - `false` → `VITE_B_A_SCREENS_PATH`, default `../../member-a/dashboard/src/screens/index.ts`, relative to `/member-b/dashboard`
- B's `tsconfig` `paths` maps `@jp/a-screens` to the mock **always**, so B's typecheck never compiles A's code.
- Required Vite settings in B's config:
  - `resolve.dedupe = ["react", "react-dom", "react/jsx-runtime"]`
  - `server.fs.allow = [".", "../../member-a/dashboard"]`
- A's `index.ts` must export exactly these named exports (no default export):
  ```ts
  export { SnapshotScreen } from "./SnapshotScreen";
  export { DispatchPlanScreen } from "./DispatchPlanScreen";
  export const A_SCREENS_CONTRACT_VERSION = "1.0.0";
  ```

### 9.3 Mount points

| Tab id | Route | Label | Owner | Component |
|---|---|---|---|---|
| `snapshot` | `/snapshot` | Crisis snapshot | A | `SnapshotScreen` |
| `plan` | `/plan` | Dispatch plan | A | `DispatchPlanScreen` |
| `fairness` | `/fairness` | Fairness & debt | B | B-internal |
| `compare` | `/compare` | Compare | B | B-internal |
| `replan` | `/replan` | Replan | B | B-internal |
| — | `/login` | — | B | B-internal |

The default route after login is `/snapshot`. Each screen renders inside a shell container `<div class="jpb-screen-slot" data-tab="{tabId}">`. The container is full width, has min-height `calc(100vh - 112px)` and 16 px padding, and sits under a 112 px header plus tab bar.

### 9.4 Props contract
Both members copy this block **verbatim** into their own `dashboard/src/contract/types.ts`.

```ts
import type { ReactNode } from "react";

export const SCREEN_CONTRACT_VERSION = "1.0.0";

export type TabId = "snapshot" | "plan" | "fairness" | "compare" | "replan";
export type Strategy = "jalpriority" | "fcfs" | "nearest_first";
export type PlanStatus = "recommended" | "approved" | "edited" | "rejected" | "superseded";
export type ReasonCode =
  | "HIGH_PREDICTED_SHORTFALL" | "HIGH_DEBT" | "HIGH_VULNERABILITY" | "HEAT_ALERT"
  | "LOWER_RELATIVE_PRIORITY" | "FULLY_SERVED" | "PARTIALLY_SERVED" | "NOT_SERVED"
  | "NO_SHORTFALL" | "SUPPLY_EXHAUSTED" | "TANKER_CAPACITY_LIMIT" | "ROUTE_LIMIT"
  | "TRAVEL_COST_TRADEOFF" | "TIME_LIMITED_SOLUTION" | "FCFS_ORDER" | "NEAREST_FIRST_ORDER";

export interface Weights { urgency: number; debt: number; vulnerability: number; travel: number; }
export interface Reason { code: ReasonCode; params: Record<string, number | string | boolean | null>; }
export interface Delivery { tanker_id: string; stop_order: number; litres: number; }
export interface Allocation {
  area_id: string; rank: number;
  predicted_shortfall_litres: number; allocated_litres: number; unmet_litres: number; coverage_ratio: number;
  priority_weight: number; urgency: number; debt_litres_used: number; debt_normalized: number; vulnerability_used: number;
  deliveries: Delivery[]; reasons: Reason[]; reason_text: string; is_unserved_remainder: boolean;
}
export interface RouteStop { stop_order: number; area_id: string; litres: number; }
export interface Route {
  tanker_id: string; status: "assigned" | "idle" | "unavailable";
  capacity_litres: number; max_route_km: number; load_litres: number; route_km: number;
  stops: RouteStop[]; path: string[];
}
export interface PlanResult {
  schema_version: "1.0"; result_id: string; strategy: Strategy;
  scenario_id: string; service_area_id: string; window_index: number; generated_at: string;
  weights: Weights;
  solver: { engine: string; status: "optimal" | "feasible" | "time_limited" | "heuristic"; solve_time_ms: number; time_limit_s: number | null };
  prediction: { model_version: string; latency_ms: number; run_mae_litres: number | null };
  totals: { supply_available_litres: number; tanker_capacity_available_litres: number; shortfall_litres: number; allocated_litres: number; unmet_litres: number; travel_km: number };
  allocations: Allocation[]; routes: Route[];
  unserved: { area_id: string; unmet_litres: number; reason_code: ReasonCode }[];
  constraint_check: { water_cap_ok: boolean; capacity_ok: boolean; route_limit_ok: boolean; unavailable_tanker_ok: boolean; violations: { code: string; detail: string }[] };
}
export interface Metrics {
  schema_version: "1.0";
  priority_weighted_unmet: number; deprivation_debt_remaining_litres: number; total_unmet_litres: number;
  min_coverage_ratio: number; total_travel_km: number; constraint_violations: number; replanning_time_ms: number | null;
}
export interface Decision { decision: "approved" | "edited" | "rejected"; officer_id: string; note: string | null; decided_at: string; next_plan_id: string | null; }
export interface PlanRecord {
  schema_version: "1.0"; plan_id: string; status: PlanStatus; created_at: string; created_by: string;
  scenario_id: string; service_area_id: string; window_index: number;
  supersedes_plan_id: string | null; replan_of_plan_id: string | null;
  disruption: { type: "tanker_down" | "supply_cut" | "heat_alert"; params: Record<string, number | string> } | null;
  is_synthetic: true; result: PlanResult; metrics: Metrics; decision: Decision | null;
}

export type TrackEvent =
  | { name: "scenario_loaded"; scenario_id: string; props: { n_areas: number; supply_litres: number; tankers_available: number } }
  | { name: "explanation_viewed"; scenario_id: string; props: { plan_id: string; area_id: string } };

export interface ScreenContext {
  aApiBase: string;            // always "/a/v1" in the shell
  token: string;               // send as Authorization: Bearer <token>
  officerId: string;
  role: "dispatcher" | "admin";
  scenarioId: string | null;   // currently selected scenario (shell-owned state)
  onScenarioChange: (scenarioId: string) => void;
  onTrack: (event: TrackEvent) => void;
  onOpenTab: (tab: TabId) => void;
}

export interface SnapshotScreenProps extends ScreenContext {}

export interface DispatchPlanScreenProps extends ScreenContext {
  plan: PlanRecord | null;        // latest plan for scenarioId, or null
  generating: boolean;            // true while the shell is creating a plan
  generateError: string | null;   // human-readable error from the last attempt
  onGeneratePlan: () => void;     // the shell creates a plan with current weights
  actionsSlot: ReactNode;         // B's approve/edit/reject bar; render verbatim above the plan table
}
```

### 9.5 Rules for A's screens
1. They are functional React components. Local state only: no providers, no global stores, no router.
2. Network calls go only to `${aApiBase}/…` with the bearer token. They must never call `/b/*` and never read `import.meta.env`.
3. All styling is scoped with CSS Modules or the `jpa-` prefix. The only global CSS allowed is `leaflet/dist/leaflet.css`. No `body` or `html` styles, and no `position: fixed`.
4. Use the `--jp-*` design tokens (§9.6) for colour and typography.
5. A 401 from A's API shows an inline error, "Session expired, please log in again". The screen must not crash or redirect.
6. Every screen must render correctly when `scenarioId` is null (show a prompt) and when `plan` is null (show an empty state with a Generate button).

### 9.6 Design tokens
Defined by B's shell on `:root`. A's harness defines identical values.
```css
:root {
  --jp-bg: #f6f7f9; --jp-surface: #ffffff; --jp-text: #111827; --jp-muted: #6b7280; --jp-border: #e5e7eb;
  --jp-accent: #0e7490; --jp-accent-weak: #cffafe;
  --jp-danger: #b91c1c; --jp-danger-weak: #fee2e2;
  --jp-warn: #b45309; --jp-ok: #15803d;
  --jp-strat-jalpriority: #0e7490; --jp-strat-fcfs: #6b7280; --jp-strat-nearest: #a16207;
  --jp-font: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  --jp-mono: "JetBrains Mono", ui-monospace, monospace;
  --jp-radius: 8px; --jp-space: 8px;
}
```
Unserved remainders are always shown in `--jp-danger` (PRD: "unserved remainder in red").

### 9.7 Frontend dependency pins
- **Both members, exact versions:** `react@18.3.1`, `react-dom@18.3.1`, `@types/react@18.3.12`, `@types/react-dom@18.3.1`, `typescript@5.6.3`, `vite@5.4.11`, `@vitejs/plugin-react@4.3.4`. Node 20.x.
- **A only:** `leaflet@1.9.4`, `react-leaflet@4.2.1`, `@types/leaflet@1.9.14`.
- **B only:** `react-router-dom@6.28.0`, `recharts@2.13.3`.
- No other runtime dependency may be imported by A's screens.

---

## 10. Contract tests (each side writes its own, inside its own folder)
- **Fixtures.** Each member copies the §5 JSON examples into their own fixtures folder, with these names:
  - `contract_scenario_example.json` (§5.3)
  - `contract_scenario_summary_example.json`
  - `contract_prediction_example.json` (§5.4)
  - `contract_model_info_example.json` (§5.5)
  - `contract_solve_request_example.json` (§6)
  - `contract_plan_result_example.json` (§5.6)
  - `contract_metrics_example.json` (§5.7)
  - `contract_plan_record_example.json` (§5.8, with the full `result`)
  - `contract_event_example.json` (§5.9)
- **Both sides:**
  - Pydantic models with `extra="forbid"` parse every fixture.
  - The F-formulas reproduce the fixture numbers (§5.11 worked check).
  - TypeScript `contract/types.ts` matches §9.4 byte for byte. A test reads `/contracts/CONTRACT.md`, extracts the §9.4 code block, and compares it after whitespace normalisation.
- **A additionally:** every `/a/v1` endpoint's live response, from the real app via TestClient, validates against its strict model and satisfies F-1 to F-8 and F-15 = 0.
- **B additionally:** every output of B's mock A client validates against the same strict models and obeys §5.12. B's `compute_metrics(§5.6 example, debt, λ=0.3)` equals §5.7 exactly. With `B_CONTRACT_LIVE_A=1`, the same suite runs against the real A (used at merge).

## 11. Change control
- During the build: no edits. Discrepancies go to `/member-x/CONTRACT_ISSUES.md` with the section number, the problem, the workaround in use and a proposed fix.
- At merge: both humans review both issue files. Any agreed change bumps the contract to `1.0.1` and is applied by both members' code in `/member-*/` on the merged branch, plus the corresponding note in `/integration/README.md`.
