# MEMBER_B.md — "Fairness, Evidence & Control" slice (JalPriority AI)

Build this half **end to end from this file plus `/contracts/CONTRACT.md` alone**. Where the two disagree, CONTRACT.md wins; log the conflict in `/member-b/CONTRACT_ISSUES.md`.

---

## 1. Scope and acceptance criteria

**User stories owned:** US-6 (debt over time), US-7 (fairness weights), US-8 (disruption replan with diff), US-9 (baseline comparison), US-10 (approve / edit / reject).
**PRD capabilities owned:** F5, F7, F8, F10, the F9 dashboard shell, the 7-metric computation, auth, audit log and event tracking. **Screens:** 3 Fairness & debt, 4 Compare, 5 Replan, plus Login and the shell. **AWS:** DynamoDB, B's Lambda and API Gateway.

B consumes A's API (CONTRACT §6) and A's two screens (CONTRACT §9). During the build, **both are mocked** (§5).

### B1 — Auth
- `POST /b/v1/auth/login` `{"username":"officer_01","password":"…"}` → 200 `{"token","officer_id","role","expires_at"}`. A wrong password gives 401 `INVALID_CREDENTIALS`.
- Users come from `B_DEMO_USERS` (format `user:password:role`, comma-separated). Passwords are hashed in memory at startup with `hashlib.scrypt`.
- JWT per CONTRACT §8: HS256, `iss`, `aud`, `sub = officer_id`, `role`, 8 h expiry.
- `GET /b/v1/auth/me` → `{officer_id, role, expires_at}`.
- All other `/b/v1/*` routes require a valid token, except `/b/v1/health`. Both `dispatcher` and `admin` may do everything.
- The service-token minter for calls to A: `sub = "svc-b"`, `role = "service"`, 5 min expiry, cached for 4 min.

### B2 — Plan orchestration (US-3 entry point, PRD "plan service")
`POST /b/v1/plans` `{"scenario_id": "...", "weights": Weights | omitted, "time_limit_s": 8.0}` → 201 PlanRecord (§5.8). Steps:
1. Resolve weights: request weights, otherwise the stored weights for the service area, otherwise the defaults (§5.2). Apply the feature flags: `B_FF_VULNERABILITY=false` → `vulnerability = 0`; `B_FF_FAIRNESS_WEIGHTS=false` → ignore the request weights and use the defaults.
2. Fetch the scenario from A. Seed the ledger if needed (B4). `debt_by_area` = ledger debt after window `window_index − 1`.
3. Call A `POST /a/v1/plans/solve`. Compute Metrics (B6) with `λ = B_DEBT_DECAY`.
4. Persist the PlanRecord with status `recommended`. Write an audit entry `plan_generated`. Emit `prediction_generated` and `plan_generated` (CONTRACT §7).

Other routes:
- `GET /b/v1/plans/{plan_id}` → PlanRecord, or 404 `PLAN_NOT_FOUND`.
- `GET /b/v1/plans?scenario_id=&status=&latest=true` → `{"plans":[PlanRecord…]}`, sorted by `created_at` descending. `latest=true` returns at most 1 plan.

Error handling:
- A unreachable or timed out (`B_A_TIMEOUT_S`, default 15) → 502 `UPSTREAM_A_UNAVAILABLE`.
- A returns an error → 502 `UPSTREAM_A_ERROR` with `details.upstream`.
- A returns `SCENARIO_NOT_FOUND` → 404, passed through.
- Forward `X-Request-ID` to A.

### B3 — A client + mock A
- `jp_b/clients/a_client.py` defines a `Protocol` with `list_scenarios`, `get_scenario`, `apply_disruption`, `get_predictions`, `solve`, `get_model_info`, `health`.
- `a_http.py` is the real client (httpx, service token, timeouts, error mapping).
- `a_mock.py` is the mock (§5). Chosen by a **single flag**, `B_USE_MOCK_A`.
- Passthrough routes so B's UI never calls `/a/*` itself:
  - `GET /b/v1/scenarios` → `{"scenarios":[ScenarioSummary…]}`
  - `GET /b/v1/scenarios/{scenario_id}` → Scenario
  - `GET /b/v1/scenarios/{scenario_id}/predictions` → PredictionSet

### F5 — Deprivation-debt ledger (B4)
- Keyed by `service_area_id`. One row per (area, window) with `debt_litres`, `unmet_litres`, `source` (`history` \| `approved_plan`), `plan_id`, `updated_at`.
- **Seeding:** the first time a service area is seen, compute windows 1..(current − 1) from `areas[].history` with F-10, where `u = max(0, need − received)`. On the §5.3 example this gives a debt after window 5 of `{ar_01: 0, ar_02: 8000, ar_03: 12000}`.
- **Update on approval:** for the plan's window w, `D_w = F-10(D_{w−1}, u_i)` using the plan's `unmet_litres`. Approving another plan for the same (service area, window), for example after a replan, **replaces** that window's rows (idempotent).
- Policy params: `B_DEBT_DECAY` (λ, default 0.3) and `B_DEBT_CAP_LITRES` (empty = no cap). Both are shown on Screen 3.
- `GET /b/v1/ledger?scenario_id=…` (resolves the service area and seeds if needed) →
  ```json
  {"service_area_id":"sa_01","decay":0.3,"cap_litres":null,"windows":[1,2,3,4,5],
   "areas":[{"area_id":"ar_03","name":"Ward 03 (synthetic)","current_debt_litres":12000,
             "series":[{"window_index":4,"debt_litres":6000,"unmet_litres":6000,"source":"history","plan_id":null},
                       {"window_index":5,"debt_litres":12000,"unmet_litres":7800,"source":"history","plan_id":null}]}],
   "totals":[{"window_index":5,"debt_litres":20000}]}
  ```

### F7 — Baselines (B5) and comparison (B7)
- `fcfs`: order areas by `request_time` ascending (ties by `area_id`).
- `nearest_first`: order by `distance_km[depot][area]` ascending (ties by `area_id`).
- For both baselines:
  - **Target** per area = `requested_litres`, rounded down to 100 L. Areas with `requested_litres = 0` are skipped.
  - **Filling:** walk areas in order. Fill the current available tanker (tankers sorted by `tanker_id`). When a tanker is full, continue the same area on the next tanker. Stop when total water S is exhausted.
  - **Route limit:** before adding a stop, check that the route in service order, including the return to depot, stays ≤ `max_route_km`. If not, try the next tanker. If no tanker fits, the area is unserved with `ROUTE_LIMIT`.
  - **Route and path:** in service order.
  - **Allocation fields:** `allocated` = delivered (may exceed `sf_i`; that is the baselines' inefficiency); `unmet` per F-6 against `predicted_shortfall_litres` from the same PredictionSet; `priority_weight` and the other fields per F-2 to F-5 with the comparison weights and debt; `rank` = service position (unserved last).
  - **Reasons:** `FCFS_ORDER` or `NEAREST_FIRST_ORDER` plus the served-state code plus a cause code. `reason_text` uses templates.
  - **Solver block:** `engine` `baseline-fcfs` or `baseline-nearest`, `status` `heuristic`.
  - Output: a PlanResult (§5.6) with `result_id` `res_…`.
- Golden test on the §5.3 example with S = 20000:
  - FCFS → ar_01 gets 20000 (tk_01 12000 + tk_02 8000); ar_02 and ar_03 get 0
  - total unmet = 15000; min coverage = 0.0; PWU = 2.2144·9000 + 2.6857·6000 = **36043.8**
  - nearest-first gives the same allocation on this example
- `POST /b/v1/comparisons` `{"scenario_id": "...", "weights": Weights | omitted}` →
  ```json
  {"schema_version":"1.0","scenario_id":"scn_sa01_w06","weights":{…},"generated_at":"…","is_simulation":true,
   "plan_id":"pln_…",
   "results":{"jalpriority":PlanResult,"fcfs":PlanResult,"nearest_first":PlanResult},
   "metrics":{"jalpriority":Metrics,"fcfs":Metrics,"nearest_first":Metrics},
   "best_by_metric":{"priority_weighted_unmet":"jalpriority","deprivation_debt_remaining_litres":"jalpriority","total_unmet_litres":"jalpriority","min_coverage_ratio":"jalpriority","total_travel_km":"nearest_first","constraint_violations":"tie"}}
  ```
  The JalPriority result reuses the latest `recommended` or `approved` PlanRecord for the same scenario and weights, otherwise creates one (B2). Emit 2 `plan_generated` events (baselines, `plan_id: null`).

### Metrics (B6)
- `compute_metrics(result, debt_by_area, decay, cap, replanning_time_ms=None) → Metrics` is a pure function implementing F-9 to F-15.
- On the §5.6 example with debt `{0, 8000, 12000}` and λ = 0.3, it returns §5.7 **exactly**.
- It is applied identically to all 3 strategies. Constraint violations are recomputed independently from the scenario; B never trusts `constraint_check`.

### F8 — Disruption replanning (B8)
`POST /b/v1/replans` `{"plan_id": "pln_…", "disruption": {"type": "tanker_down", "params": {"tanker_id": "tk_01"}}}` → 201:
```json
{"schema_version":"1.0","previous_plan_id":"pln_…","derived_scenario_id":"scn_sa01_w06__d1_3f9a2c",
 "disruption":{"type":"tanker_down","params":{"tanker_id":"tk_01"}},
 "replanning_time_ms":1830,
 "new_plan":PlanRecord,
 "diff":{"areas":[{"area_id":"ar_01","litres_before":5000,"litres_after":0,"delta":-5000,
                   "tankers_before":["tk_01"],"tankers_after":[],"order_before":[2],"order_after":[],
                   "change":"dropped"}],
         "summary":{"areas_changed":3,"litres_moved":11000,"metrics_before":Metrics,"metrics_after":Metrics}}}
```
- Steps:
  1. Start the timer.
  2. A `apply_disruption`.
  3. B2 on the derived scenario with the **same weights and the same debt** as the previous plan, and `time_limit_s = B_REPLAN_TIME_LIMIT_S` (default 3.0).
  4. Compute the diff.
  5. Stop the timer.
- On the new plan: `replan_of_plan_id`, `disruption` and `metrics.replanning_time_ms` are set. If the previous plan was `recommended`, it becomes `superseded`; if `approved`, it stays approved.
- Emit `disruption_applied` and `replan_completed`. Write an audit entry `replan_applied`.
- `change` ∈ `increased` | `decreased` | `unchanged` | `newly_served` | `dropped` | `reassigned` (same litres, different tanker or order). `litres_moved = Σ|delta| / 2`.
- Target: under 5 s end to end. Show the measured time on screen.
- `B_FF_REPLAN=false` → 403 `FEATURE_DISABLED`.

### F10 — Human approval (B9) and audit
`POST /b/v1/plans/{plan_id}/decision` `{"decision": "approved" | "edited" | "rejected", "note": "..." | null, "revised_weights": Weights | null}` →
```json
{"plan":PlanRecord,"next_plan":PlanRecord | null,"ledger_updated":true}
```
- `approved` → status `approved`, ledger update (F5). `ledger_updated` is true.
- `edited` → `revised_weights` is required (422 otherwise). The status becomes `edited`, and a new plan is created through B2 with the revised weights and `supersedes_plan_id` set. `decision.next_plan_id` is set.
- `rejected` → status `rejected`; the note is required (422 otherwise).
- A non-`recommended` plan → 409 `PLAN_ALREADY_DECIDED`.
- Every decision writes an audit entry and emits `plan_decision`.
- `GET /b/v1/audit?limit=50&plan_id=` → `{"entries":[{"audit_id","ts","officer_id","action","plan_id","scenario_id","details"}]}`. Actions: `login`, `plan_generated`, `plan_approved`, `plan_edited`, `plan_rejected`, `weights_changed`, `replan_applied`.

### Weights and flags (B10)
- `GET /b/v1/weights?service_area_id=sa_01` → `{"service_area_id","weights","updated_at","updated_by","flags":{"fairness_weights":bool,"vulnerability":bool,"replan":bool},"defaults":Weights,"debt_policy":{"decay":0.3,"cap_litres":null}}`.
- `PUT /b/v1/weights` `{"service_area_id":"sa_01","weights":Weights}` → same shape. Validate the §5.2 ranges. Emit `weights_changed` and write an audit entry.

### Events (B11)
- `POST /b/v1/events` `{"name": "...", "scenario_id": "..." | null, "props": {...}}` → 202 `{"event_id": "evt_…"}`.
- Only the 9 names in CONTRACT §7 are accepted (422 otherwise). The shell's `onTrack` uses this route.
- `GET /b/v1/events?scenario_id=&name=&limit=` → `{"events":[Event…]}`. Events let any demo run be replayed.

### Storage (B12)
- `B_STORE_MODE=sqlite` (default; file `B_SQLITE_PATH`) or `dynamodb` (prefix `B_DDB_TABLE_PREFIX`, default `jp_b_`).
- One repository interface per entity, with identical behaviour in both modes.

| Table | Key | Notes |
|---|---|---|
| `jp_b_plans` | `plan_id` | GSI/index `scenario_id` + `created_at`; stores the full PlanRecord JSON |
| `jp_b_ledger` | `pk = "{service_area_id}#{area_id}"`, `sk = window_index` | |
| `jp_b_events` | `event_id` | index `scenario_id` + `ts` |
| `jp_b_audit` | `audit_id` | index `ts` |
| `jp_b_weights` | `service_area_id` | |

### Dashboard shell (B13; owner of the single SPA, CONTRACT §9)
- **Routes:** `/login`, then the five tabs per CONTRACT §9.3. Unauthenticated users are redirected to `/login`; a 401 from B logs the user out.
- **Header:** product name "JalPriority AI", the **SYNTHETIC DATA · SIMULATION RESULTS** badge, the current scenario id chip, officer id and role, Logout. Then the tab bar.
- **Global state** (React context inside the shell): `token`, `officerId`, `role`, `scenarioId` (persisted to `sessionStorage["jp.b.scenarioId"]`), `weights`, `plan` (latest for the scenario), `generating` and `generateError`.
- **Behaviour:**
  - Changing the scenario loads the latest plan for it (`GET /b/v1/plans?scenario_id=&latest=true`).
  - `onGeneratePlan` → `POST /b/v1/plans`.
  - `onTrack` → `POST /b/v1/events`.
  - `onOpenTab` → navigate.
- **Mounting** `SnapshotScreen` and `DispatchPlanScreen` from `@jp/a-screens` with the exact §9.4 props. `aApiBase = "/a/v1"`. `actionsSlot = <ApprovalBar plan={plan} …/>`.
- **ApprovalBar:** Approve (confirm dialog), Edit weights (inline 4-slider popover → `edited` with `revised_weights`), Reject (note required). It is disabled unless the status is `recommended`, shows the status pill and decision details, and on success updates `plan` to `next_plan ?? plan`.
- **Design tokens:** CONTRACT §9.6 on `:root`, in `src/styles/tokens.css`. Body background is `--jp-bg`.
- **Vite config:** the alias, dedupe and `fs.allow` per CONTRACT §9.2; dev proxy `/b` → `http://localhost:8002` and `/a` → `http://localhost:8001`; `server.port = 5173`.

### Screen 3 — Fairness & debt (B14, US-6 and US-7)
- **Weight sliders** for urgency, debt, vulnerability (disabled with a tooltip when the flag is off) and travel. They show the numeric value, the default, and a one-line plain-language meaning. Buttons: "Save weights" (PUT) and "Save & regenerate plan" (PUT then `onGeneratePlan`). Show the priority formula `p = 1 + α·U + β·D̂ + γ·V` with the live values.
- **Debt-over-windows line chart** (recharts): one line per area plus a total. Points from `approved_plan` are marked differently from `history`. Show the debt policy (λ, cap) and a 2-sentence plain-language explanation of deprivation debt.
- **Coverage bar chart** for the current plan, per area (`coverage_ratio`), with a red bar where coverage < 0.25.
- **Table:** area, current debt, priority weight, coverage, unmet (red).

### Screen 4 — Compare (B15, US-9)
- A "Run comparison" button using the current scenario and weights. Also auto-runs on first open if a scenario is selected. Emits `comparison_viewed`.
- **Metrics table:** 7 rows × 3 columns (FCFS, Nearest-first, JalPriority). Each row shows a direction arrow (lower or higher is better), highlights the best, and shows JalPriority's delta vs each baseline. Constraint violations are shown as a pass/fail badge.
- **Per-area coverage grouped bar chart** by strategy. A callout lists "Areas left with almost nothing (< 10% coverage)" per strategy; this is the demo's first beat.
- A footer disclaimer: synthetic scenario, simulation results, same resources and same yardstick for all strategies.

### Screen 5 — Replan (B16, US-8)
- Shows the current plan summary. If there is no plan: an empty state linking to the Plan tab.
- **Disruption picker:** `tanker_down` (dropdown of available tankers from `GET /b/v1/scenarios/{id}`), `supply_cut` (slider 5–90%), `heat_alert` (select 0–3, excluding the current level). An "Apply & replan" button.
- **Result:** large replanning-time readout (green < 5 s, amber otherwise), metrics before → after, and a diff table colour-coded by `change` (dropped and decreased in red, newly_served and increased in green).
- Buttons: "Open revised plan" (sets the shell `plan` to `new_plan` and the `scenarioId` to the derived id, then goes to `/plan`) and the ApprovalBar for the new plan.

### Effort (story points; the same relative scale as MEMBER_A)

| ID | Module | SP |
|---|---|---|
| B1 | Auth: login, JWT issue, demo users, service tokens | 3 |
| B2 | Plan orchestration + plan store + passthrough routes | 5 |
| B3 | A client protocol, HTTP client, mock A (fixtures, mock predictor and greedy solver, §5.12 replica) | 3 |
| B4 | Deprivation-debt ledger (seed, update, replace, policy) | 5 |
| B5 | Baselines FCFS + nearest-first, with tanker filling and route checks | 5 |
| B6 | Metrics (F-9 to F-15) + independent violation check | 3 |
| B7 | Comparison endpoint | 2 |
| B8 | Replan: orchestration, diff, timing | 5 |
| B9 | Decisions (approve, edit, reject) + audit log | 3 |
| B10 | Weights store + feature flags | 2 |
| B11 | Events ingestion + query | 2 |
| B12 | Storage layer: SQLite + DynamoDB repositories | 5 |
| B13 | Dashboard shell: login, header, tabs, state, mount + mock A screens, ApprovalBar, tokens | 5 |
| B14 | Screen 3 Fairness & debt | 5 |
| B15 | Screen 4 Compare | 3 |
| B16 | Screen 5 Replan | 5 |
| B17 | Tests: unit, golden, contract, e2e with mock | 3 |
| B18 | Dockerfile + SAM template (DynamoDB tables, Lambda, HTTP API) | 3 |
| | **Total B** | **67** |

---

## 2. Ownership manifest

**B owns everything under `/member-b/**`**, specifically:

```text
member-b/
├── MEMBER_B.md                 # this file (from T0)
├── CLAUDE.md                   # §9 block, saved verbatim
├── README.md
├── CONTRACT_ISSUES.md
├── .gitignore  .dockerignore  .env.example  .env (gitignored)
├── backend/
│   ├── pyproject.toml          # package jp_b, python >=3.11,<3.13
│   ├── requirements.txt  requirements-dev.txt
│   ├── Dockerfile              # build context = /member-b
│   └── jp_b/
│       ├── __init__.py  main.py  config.py  logging_setup.py
│       ├── api/        deps.py auth.py errors.py schemas.py
│       │               routes_health.py routes_auth.py routes_scenarios.py routes_plans.py routes_decisions.py
│       │               routes_ledger.py routes_comparisons.py routes_replans.py routes_weights.py routes_events.py routes_audit.py
│       ├── services/   plan_service.py ledger_service.py baseline_service.py metrics_service.py comparison_service.py
│       │               replan_service.py decision_service.py weights_service.py event_service.py audit_service.py auth_service.py
│       ├── domain/     entities.py priority.py metrics.py constraints.py diff.py ledger_math.py
│       ├── clients/    a_client.py a_http.py a_mock.py fixtures/*.json
│       └── repositories/ base.py sqlite_repo.py dynamodb_repo.py
├── var/                        # SQLite file (gitignored)
├── scripts/            run_dev.sh  create_ddb_tables.py  check_ownership.sh
├── tests/              unit/  golden/  contract/  e2e/  fixtures/  conftest.py
├── dashboard/
│   ├── package.json  package-lock.json  tsconfig.json  vite.config.ts  index.html  .env.example
│   └── src/
│       ├── main.tsx  App.tsx  router.tsx
│       ├── contract/types.ts          # verbatim CONTRACT §9.4
│       ├── shell/  ShellContext.tsx Header.tsx TabBar.tsx ApprovalBar.tsx ScreenSlot.tsx
│       ├── screens/ LoginScreen.tsx FairnessDebtScreen.tsx CompareScreen.tsx ReplanScreen.tsx components/*.tsx
│       ├── api/    bClient.ts
│       ├── mocks/  aScreens.tsx         # mock of @jp/a-screens (same exports and props)
│       └── styles/ tokens.css shell.module.css
└── infra/              template.yaml  samconfig.toml
```

| Resource type | B owns |
|---|---|
| Routes | `/b/v1/health`, `/b/v1/auth/login`, `/b/v1/auth/me`, `/b/v1/scenarios`, `/b/v1/scenarios/{scenario_id}`, `/b/v1/scenarios/{scenario_id}/predictions`, `/b/v1/plans`, `/b/v1/plans/{plan_id}`, `/b/v1/plans/{plan_id}/decision`, `/b/v1/ledger`, `/b/v1/comparisons`, `/b/v1/replans`, `/b/v1/weights`, `/b/v1/events`, `/b/v1/audit` |
| Tables | `jp_b_plans`, `jp_b_ledger`, `jp_b_events`, `jp_b_audit`, `jp_b_weights` (SQLite and DynamoDB) |
| UI | The shell, Login, FairnessDebtScreen, CompareScreen, ReplanScreen, ApprovalBar, the mock A screens; everything under `/member-b/dashboard` |
| Ports | 8002 (API), 5173 (dashboard dev) |
| Names | Python `jp_b`; npm `@jp/b-dashboard`; Docker image `jp-b-api`; SAM stack `jalpriority-b`; CSS `jpb-`; storage `jp.b.*`; log `service: "jp-b"` |
| Events | Emits 7 events (CONTRACT §7, all except `scenario_loaded` and `explanation_viewed`); stores all 9 |

### DO NOT TOUCH
- `/contracts/**` (read only)
- `/member-a/**` (never edit it; do not read it for hints; at runtime it is only referenced through the alias path config)
- `/integration/**`
- any repo-root file; do not create one
- any `A_*`, `VITE_A_*` or new `SHARED_*` variable
- any `/a/*` route
- S3 bucket `jalpriority-a-*`; SageMaker
- port 8001 or 5174
- the scenario generator, model, optimizer, routing, explanations, Snapshot and Dispatch plan screens

---

## 3. Tech stack and dependencies

**Backend** (`/member-b/backend/requirements.txt`; if a pin fails on your platform, move to the nearest working patch release in this file only):
```text
fastapi==0.115.6
uvicorn[standard]==0.32.1
pydantic==2.10.4
pydantic-settings==2.7.0
httpx==0.28.1
pyjwt==2.10.1
boto3==1.35.90
mangum==0.19.0
orjson==3.10.12
```
`requirements-dev.txt`: `pytest==8.3.4`, `respx==0.22.0`, `moto[dynamodb]==5.0.24`, `freezegun==1.5.1`, `ruff==0.8.4`.

**Frontend** (`/member-b/dashboard/package.json`, exact pins per CONTRACT §9.7): `react 18.3.1`, `react-dom 18.3.1`, `react-router-dom 6.28.0`, `recharts 2.13.3`. Dev: `typescript 5.6.3`, `vite 5.4.11`, `@vitejs/plugin-react 4.3.4`, `@types/react 18.3.12`, `@types/react-dom 18.3.1`, `vitest 2.1.8`, `@testing-library/react 16.1.0`, `jsdom 25.0.1`. npm scripts: `dev` (port 5173), `build`, `typecheck`, `test`.

**Env vars** (`/member-b/.env.example`):

| Var | Default | Purpose |
|---|---|---|
| `B_PORT` | `8002` | API port |
| `B_STORE_MODE` | `sqlite` | `sqlite` \| `dynamodb` |
| `B_SQLITE_PATH` | `<member-b>/var/jp_b.sqlite3` (in Docker: `/app/var/jp_b.sqlite3`) | |
| `B_DDB_TABLE_PREFIX` | `jp_b_` | |
| `B_AWS_REGION` | `ap-south-1` | |
| `B_UPSTREAM_A_URL` | `http://localhost:8001` | real A base URL |
| `B_USE_MOCK_A` | `true` | **the** swap flag for A's API |
| `B_A_TIMEOUT_S` | `15` | |
| `B_REPLAN_TIME_LIMIT_S` | `3.0` | solver limit sent on replans |
| `B_DEMO_USERS` | `officer_01:jal-demo-2026:dispatcher,admin_01:jal-admin-2026:admin` | demo only |
| `B_DEBT_DECAY` | `0.3` | λ |
| `B_DEBT_CAP_LITRES` | (empty) | optional cap |
| `B_FF_FAIRNESS_WEIGHTS` | `true` | feature flag |
| `B_FF_VULNERABILITY` | `true` | feature flag |
| `B_FF_REPLAN` | `true` | feature flag |
| `B_CORS_ORIGINS` | `http://localhost:5173` | |
| `B_CONTRACT_LIVE_A` | `0` | `1` = run consumer contract tests against the real A |
| `VITE_B_USE_MOCK_A_SCREENS` | `true` | **the** swap flag for A's screens |
| `VITE_B_A_SCREENS_PATH` | `../../member-a/dashboard/src/screens/index.ts` | used when the flag above is `false` |

Under a `# SHARED (contract-owned, read-only)` heading, also list `SHARED_JWT_SECRET`, `SHARED_JWT_ISSUER`, `SHARED_JWT_AUDIENCE`, `SHARED_ENV` and `SHARED_LOG_LEVEL` with the CONTRACT §4.8 dev defaults.

---

## 4. Build order (milestones; commit after each one with the tests green)

| M | Deliverable | Done when |
|---|---|---|
| M0 | Scaffold `jp_b`: config, JSON logging, request-id middleware, error envelope (including 422 re-wrap), `/b/v1/health`, `scripts/check_ownership.sh` | Unit tests green |
| M1 | Strict Pydantic models for every CONTRACT §5 entity + the 9 contract fixtures + the `contract/types.ts` byte-compare test | Fixture tests green |
| M2 | Auth (B1) + storage layer (B12, SQLite first) | Login → token → `/me` works; repository tests green |
| M3 | A client protocol + mock A (B3) + passthrough routes + mock-conformance contract tests | Mock outputs parse strictly and obey §5.12 |
| M4 | `domain/priority.py`, `metrics.py`, `constraints.py` as pure functions | §5.11 worked check and §5.7 golden green |
| M5 | Ledger (B4) | The seeding golden gives 0 / 8000 / 12000; replace-on-reapprove test |
| M6 | Baselines (B5) | FCFS and nearest-first goldens on the §5.3 example |
| M7 | Plan orchestration (B2) + weights and flags (B10) + events (B11) + audit | e2e (mock): create a plan, list it, get it; events stored |
| M8 | Decisions (B9) | Approve updates the ledger; edit creates a superseding plan; reject requires a note; second decision → 409 |
| M9 | Comparison (B7) | 3 results + metrics + `best_by_metric` |
| M10 | Replan + diff (B8) | All 3 disruption types against the mock; diff classification tests; replanning time recorded |
| M11 | Dashboard shell (B13) with the mock A screens; Login; ApprovalBar | Login → tabs → generate → approve works against the B API with the mock A |
| M12 | Screens 3, 4, 5 (B14–B16) | Manual walkthrough of the full demo script with the mock A |
| M13 | Dockerfile (context `/member-b`, `CMD uvicorn jp_b.main:app --host 0.0.0.0 --port ${B_PORT}`, `/app/var` volume) + README | Container serves health on 8002 |
| M14 (stretch, last) | `B_STORE_MODE=dynamodb` (moto tests) + `infra/template.yaml` (5 tables on-demand, Lambda via Mangum, HTTP API, least-privilege IAM) | DynamoDB mode passes the same repository tests; local remains the default |

---

## 5. Mocks and stubs (for everything consumed from A)

### 5.1 Mock A API — `jp_b/clients/a_mock.py`, selected by `B_USE_MOCK_A=true`
- **Fixtures** (B-authored, from the contract), in `jp_b/clients/fixtures/`:
  - `scn_contract_example.json` (the CONTRACT §5.3 example)
  - `scn_sa01_w06.json`, written by B per the §5.3 schema: 8 areas, 4 tankers (capacity 10,000–12,000 L), history windows 1–5, alert 2, supply ≈ 55% of total shortfall, and at least 2 far, late, vulnerable areas with debt. These are the same demo characteristics as A's seed; the numbers will differ.
- **`list_scenarios` / `get_scenario`:** serve the fixtures plus any derived scenarios held in memory.
- **`apply_disruption`:** an exact replica of CONTRACT §5.12 (types, params validation, errors as raised exceptions mapped to the same codes, derived-id hashing, idempotency).
- **`get_predictions`:**
  - `predicted_need = round_to_100(last_history_need × (1 + 0.04 × alert_level))`
  - shortfall per F-1
  - `model_version = "mock-0"`, `latency_ms = 1`, `run_mae_litres = null`
  - `top_features`: fixed (`heat_index_c`, `last_need_litres`, `population`) with contributions at 10%, 6% and 3% of need
  - **Exception:** for `scn_contract_example` it returns the §5.4 example verbatim.
- **`solve`:** a greedy mock that obeys the contract's guarantees:
  - priority by F-5
  - serve areas in priority order up to `sf_i`, in 100 L units
  - first-fit tankers by `tanker_id` with a star route bound
  - nearest-neighbour sequencing
  - reason codes by simple rules; `engine = "mock"`, `status = "heuristic"`
  - **Exception:** for `scn_contract_example` with the §6 SolveRequest it returns the §5.6 example verbatim.
- **`get_model_info`:** the §5.5 example verbatim.
- The mock must produce 0 violations and plans that change visibly with weights, so screens 3–5 can be demoed against it.

### 5.2 Mock A screens — `dashboard/src/mocks/aScreens.tsx`, selected by `VITE_B_USE_MOCK_A_SCREENS=true`
- Exports `SnapshotScreen`, `DispatchPlanScreen` and `A_SCREENS_CONTRACT_VERSION = "1.0.0"`, typed with the exact §9.4 props.
- **SnapshotScreen mock:** a scenario `<select>` using `GET /b/v1/scenarios` (B's passthrough, not `/a`), a KPI line, and a call to `onTrack("scenario_loaded")`.
- **DispatchPlanScreen mock:** renders `actionsSlot`, a Generate button, and a plain table of `plan.result.allocations` with unserved rows in `--jp-danger`.
- These exist only to exercise the shell and props wiring. They are replaced by the real A screens at merge by flipping the flag.

---

## 6. Contract tests (`/member-b/tests/contract/`)
1. **Fixtures:** all 9 CONTRACT §10 fixtures parse under strict models.
2. **Mock conformance:** every mock-A output (all 3 disruption types, predictions, solve on both fixtures, model info, list) parses strictly; IDs match §4.2; formulas F-1 to F-8 hold; F-15 = 0.
3. **Goldens:**
   - `compute_metrics(§5.6, {ar_01: 0, ar_02: 8000, ar_03: 12000}, λ=0.3)` equals §5.7
   - ledger seeding on §5.3 gives `{0, 8000, 12000}`
   - FCFS on §5.3 gives the B5 golden
   - priority for the 3 example areas equals 1.4093 / 2.2144 / 2.6857
4. **Provided shapes:** PlanRecord responses from `/b/v1/plans` parse against the strict §5.8 model; stored events parse against §5.9 and use only §7 names and props keys.
5. **Consumer suite against the real A** (skipped unless `B_CONTRACT_LIVE_A=1`): the same assertions as #2, run through `a_http.py` against `B_UPSTREAM_A_URL`. This is the main merge-time check.
6. **Frontend:**
   - `src/contract/types.ts` equals CONTRACT §9.4 after whitespace normalisation (the test reads `../../contracts/CONTRACT.md`)
   - `mocks/aScreens.tsx` assigns to `React.FC<…Props>` types
   - the shell passes every required prop (type test)
   - a vitest render test checks that `onTrack` posts to `/b/v1/events` with the exact `TrackEvent` shape
   - `vite.config.ts` contains the alias, dedupe and `fs.allow` per §9.2
7. **Isolation lint:** a grep test asserts that `dashboard/src/**`, excluding the alias config, contains no `"/a/v1"` string literal except the single `aApiBase = "/a/v1"` constant passed as a prop.

---

## 7. Definition of done
- [ ] Milestones M0–M13 complete. M14 either works or is marked "not deployed" in the README.
- [ ] `pytest` is fully green (unit, golden, contract with the mock, e2e with the mock). `npm run typecheck && npm test && npm run build` are green.
- [ ] Full demo script with `B_USE_MOCK_A=true`: login → snapshot → generate → compare (FCFS starves at least one area) → fairness (change weights, regenerate) → approve (ledger gains window 6) → replan with tanker_down (diff shown, time < 5 s) → approve the revised plan (window 6 rows replaced).
- [ ] Every PRD event is stored with exact props. The audit log records every generate, decision, weights change and replan.
- [ ] `scripts/check_ownership.sh` passes: only `member-b/` paths changed since `t0`.
- [ ] `docker build -f backend/Dockerfile .` (from `/member-b`) builds, and the container serves health on 8002.
- [ ] `npm run build` succeeds with `VITE_B_USE_MOCK_A_SCREENS=true`. The alias switch to the real path is implemented, even though it is not exercised until merge.
- [ ] The README covers setup, flags, debt policy, demo script and limitations.
- [ ] `CONTRACT_ISSUES.md` exists, even if it only says "None".

---

## 8. Hand-off notes for merge
- Document in the README exactly how to flip both swap flags (`B_USE_MOCK_A=false`, `VITE_B_USE_MOCK_A_SCREENS=false`).
- The mock fixtures stay in place; they are used only when the flags are true.
- `npm ci` must work from a clean checkout of `/member-b/dashboard`.

---

## 9. Claude Code operating instructions — save verbatim as `/member-b/CLAUDE.md`

```markdown
# CLAUDE.md — Member B (JalPriority AI, "Fairness, Evidence & Control" slice)

You are building Member B's half of JalPriority AI. Your working directory is `member-b/`.
Sources of truth, in order: `../contracts/CONTRACT.md` (frozen), then `./MEMBER_B.md`.

## Authority
- You have full decision authority. Run everything end to end autonomously: scaffold, install, implement, mock, test, fix, commit.
- Make every routine engineering decision yourself (library usage, file structure inside your folder, UI layout details, chart choices). Do not ask for confirmation.
- Ask the user ONLY when genuinely blocked: missing AWS credentials for a stretch step, a hard platform failure you cannot work around, or a contradiction that makes the contract impossible to satisfy (log it first, see below).

## Boundaries (non-negotiable)
- Create, edit and delete files ONLY under `member-b/`. Never touch `../contracts/`, `../member-a/`, `../integration/` or any repo-root file.
- Never modify `../contracts/CONTRACT.md`. If it seems wrong, ambiguous or impossible, append an entry to `member-b/CONTRACT_ISSUES.md` (section number, problem, workaround in use, proposed fix) and keep building against the contract as written, using the mock.
- Member A's API and screens do not exist for you yet. Always build against the mocks (`B_USE_MOCK_A=true`, `VITE_B_USE_MOCK_A_SCREENS=true`), built strictly from the contract. Do not read `../member-a/` for hints.
- Only use env vars prefixed `B_` or `VITE_B_`, plus the listed `SHARED_*` read-only vars. Only serve routes under `/b/v1`. Your browser code never calls `/a/*` itself; it only passes `aApiBase="/a/v1"` as a prop to A's screens.
- Before every commit, run `scripts/check_ownership.sh` (fails if `git diff --name-only t0...HEAD` contains any path outside `member-b/`).

## How to work
- Follow the milestone order in MEMBER_B.md §4. After each milestone: run the tests, fix until green, then commit with message `B-M<n>: <summary>`.
- Contract tests and goldens are mandatory and must stay green. Strict models in tests (`extra="forbid"`), tolerant at runtime.
- Exact field names, ID formats, error codes, formulas (CONTRACT §5.11) and rounding come from the contract. Never invent or rename fields.
- The mock A must obey every guarantee A gives (0 violations, §5.12 semantics, exact examples on the contract fixture).
- SQLite is the default store; DynamoDB (M14) is last and must never break the local default.
- Python 3.11+, a venv at `member-b/.venv`, `pip install -e backend -r backend/requirements-dev.txt`. Node 20; `npm ci` in `member-b/dashboard`.
- When finished, verify the Definition of Done checklist in MEMBER_B.md §7 item by item and report the result.
```
