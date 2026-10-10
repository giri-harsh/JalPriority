# MEMBER_A.md — "Need → Plan" slice (JalPriority AI)

Build this half **end to end from this file plus `/contracts/CONTRACT.md` alone**. Where the two disagree, CONTRACT.md wins; log the conflict in `/member-a/CONTRACT_ISSUES.md`.

---

## 1. Scope and acceptance criteria

**User stories owned:** US-1 (load crisis snapshot), US-2 (predicted shortage), US-3 (ranked allocation with litres), US-4 (tanker assignment and order), US-5 (plain-language reasons).
**PRD capabilities owned:** F1, F2, F3, F4, F6. **Screens:** 1 Crisis snapshot, 2 Dispatch plan. **AWS:** S3, SageMaker, and A's own Lambda.

### F1 — Synthetic scenario input
- A deterministic generator (`A_SEED`, default 42) writes these seed scenarios to `/member-a/data/scenarios/`:
  - `scn_sa01_w06` — main demo: alert level 2, tanker water ≈ 55% of total shortfall
  - `scn_sa01_w06_severe` — alert 3, ≈ 35%
  - `scn_sa01_w06_mild` — alert 1, ≈ 85%
- Each seed has 8 areas, 4 tankers (capacity 10,000–12,000 L, `max_route_km` 60) and history for windows 1–5, conforming exactly to CONTRACT §5.3.
- Running the generator twice gives byte-identical files.
- **Demo property** (asserted by a test on `scn_sa01_w06`). There must be at least 2 areas that each satisfy all of the following:
  - `vulnerability_index ≥ 0.6`
  - depot distance among the 3 farthest
  - `request_time` among the 3 latest
  - `requested_litres < predicted_shortfall`
  - debt after window 5 (CONTRACT F-10 with λ = 0.3 over the history) `> 0`

  Also, total predicted shortfall ≥ 1.6 × supply, and at least one near, early, low-vulnerability area with `requested_litres > predicted_shortfall`.

  This guarantees the demo moment: FCFS and nearest-first starve the far, vulnerable, late areas.
- A training dataset `/member-a/data/training/{train,test}.csv` is generated from service areas `sa_02`–`sa_41`, each with 6–10 areas and windows 1–30. The train/test split is by service area: `sa_02`–`sa_33` train, `sa_34`–`sa_41` test. `sa_01` is excluded from training.
- Suggested data-generating process (adjust freely, but keep it documented in the README):
  - need = population × 3.0 L × (1 + 0.04·max(0, max_temp_c − 35)) × (1 + 0.15·heat_exposure·alert_level/3) × lognormal(σ = 0.08), with AR(0.3) carry-over from the previous window
  - piped = need × piped_share (0.55–0.85 per area) × (1 − 0.03·alert_level)
  - historical tanker deliveries simulated nearest-first (so distant areas build debt)
  - requested = shortfall × voice, where voice ~ U(0.5, 1.5) and is negatively correlated with vulnerability; louder and nearer areas request earlier
  - road distance = haversine × 1.3
  - coordinates are synthetic, within about 6 km of (28.6692, 77.4538)
- Hidden ground truth (true need for window 6) is stored separately in `/member-a/data/truth/{scenario_id}.json` and used only for `run_mae_litres`.

### F2 — Shortage prediction
- XGBoost regressor (`n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9, random_state=42`) predicting `need_litres` for window t.
- Features (exact names): `max_temp_c`, `heat_index_c`, `alert_level`, `heat_exposure`, `population`, `last_need_litres`, `mean_need_last3_litres`, `last_received_litres`, `expected_piped_litres`.
- Baseline: last observed need. Report MAE and RMSE on the test split for both. The artefact is committed as `/member-a/models/shortage_xgb_v1.json` plus `model_meta.json`, so the API never trains at startup.
- `GET /a/v1/model` returns ModelInfo (§5.5) with real numbers. If XGBoost does not beat the baseline, report that honestly; do not tune on the test set.
- `GET /a/v1/scenarios/{id}/predictions` returns a PredictionSet (§5.4):
  - shortfall per F-1
  - `top_features` = the 3 largest |SHAP contributions| from `Booster.predict(..., pred_contribs=True)`, in litres
  - `latency_ms` measured
  - target latency under 2 s for 10 areas (expect under 200 ms locally)
- Predictions are cached in memory per `scenario_id`.

### F3 — Fairness-aware optimizer
`POST /a/v1/plans/solve` returns a PlanResult (§5.6) with:
- zero violations (F-15) on all 3 seeds plus 50 randomly generated scenarios (property test)
- `priority_weight`, `urgency`, `debt_normalized` and `vulnerability_used` equal to F-2 to F-5 at 4 dp
- `allocated_litres ≤ predicted_shortfall_litres` per area
- a response within `time_limit_s + 1.0`; on the time limit, it returns the best feasible plan with `status: "time_limited"` (never an error)
- if CP-SAT finds nothing, a greedy-by-priority fallback (`engine: "greedy-fallback"`, `status: "heuristic"`, reason `TIME_LIMITED_SOLUTION` on all areas)

**Formulation** (CP-SAT, units of 100 L, available tankers only):
- **Variables:** `y[k,i]` is an integer in `[0, cap_k/100]` (litres-units tanker k delivers to area i). `z[k,i]` is boolean, with `y[k,i] ≤ z[k,i]·cap_k/100`.
- **Constraints:**
  - `x_i = Σ_k y[k,i] ≤ floor(sf_i/100)`
  - `Σ_i y[k,i] ≤ cap_k/100`
  - `Σ_{k,i} y[k,i] ≤ floor(S/100)`
  - star route bound: `Σ_i z[k,i]·2·d(depot,i) ≤ max_route_km_k`. A TSP tour over the visited set is never longer than this bound, so route feasibility is guaranteed.
- **Unmet:** `u_i = floor(sf_i/100) − x_i`. Split `u_i = u1_i + u2_i`, where `u1_i ≤ ceil((1 − φ)·sf_i/100)` is the "normal" deficit and `u2_i` is the deficit below a coverage floor φ (`A_COVERAGE_FLOOR`, default 0.25).
- **Objective:** minimise `Σ_i P_i·(u1_i + κ·u2_i) + Σ_{k,i} T_i·z[k,i]`, where:
  - `P_i = round(p_i·1000·100)`
  - `κ = A_FLOOR_PENALTY` (default 3.0)
  - `T_i = round(δ·d(depot,i)·1000)` (travel proxy)

  This keeps the PRD objective (`Σ p·u + δ·travel`) and adds a soft minimum-coverage term that serves the PRD's "minimum neighbourhood coverage" goal. Document this in the README.
- **Solver params:** `max_time_in_seconds = time_limit_s − 0.5`, `num_workers = 8`, `random_seed = 42`. Map OPTIMAL → `optimal`, FEASIBLE → `time_limited` if the limit was hit, otherwise `feasible`.
- **Litre rounding:** deliveries are multiples of 100 L. The remainder below 100 L counts as unmet.

### F4 — Tanker assignment and routing
- For each tanker with deliveries, sequence the stops as an exact TSP by permutation (≤ 7 stops) or with OR-Tools `RoutingModel` (PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH, 0.3 s per tanker), starting and ending at the depot. Tie-break equal tours by visiting the higher-priority area first.
- `routes[]`:
  - one entry per scenario tanker
  - `assigned` / `idle` / `unavailable`
  - `stop_order` 1..n
  - `path` starts and ends at the depot
  - `route_km` per F-8 (must match the matrix within 0.01)
- Each allocation's `deliveries[]` uses the same `stop_order` as its route.

### F6 — Explanations and disclosure
- Every allocation has at least 1 reason from the closed enum (§5.10) and a non-empty `reason_text` built from templates with formatted numbers (`12,000 L`, `36%`, `2.69`). There is no free-form generation.
- Rules:
  - `HIGH_DEBT` if `D̂ ≥ 0.5` and `D > 0`
  - `HIGH_VULNERABILITY` if `V ≥ 0.6`
  - `HIGH_PREDICTED_SHORTFALL` if `U ≥ 0.3`
  - `HEAT_ALERT` if alert ≥ 2 and heat_exposure ≥ 0.7
  - exactly one of `FULLY_SERVED`, `PARTIALLY_SERVED`, `NOT_SERVED`, `NO_SHORTFALL`
  - if unmet > 0, one cause: `SUPPLY_EXHAUSTED` (total water bound binding), `TANKER_CAPACITY_LIMIT` (all available capacity used), `ROUTE_LIMIT` (no tanker can add this area within its route bound), `TRAVEL_COST_TRADEOFF` (travel proxy outweighed benefit), or `LOWER_RELATIVE_PRIORITY`
  - `LOWER_RELATIVE_PRIORITY` is also added when `p_i` is in the bottom third
- `unserved[]` lists every area with unmet > 0. Shortage is never hidden.

### Disruption endpoint (supports B's US-8)
`POST /a/v1/scenarios/{id}/disruptions` implements CONTRACT §5.12 exactly: semantics, errors, derived-id hashing and idempotency. Derived scenarios are persisted and served by `GET /a/v1/scenarios/{id}`. Their `run_mae_litres` is `null`.

### Screen 1 — Crisis snapshot (`SnapshotScreen`)
- A scenario picker lists `GET /a/v1/scenarios`; choosing one calls `onScenarioChange(id)`. If `scenarioId` is set, it loads `/scenarios/{id}` and `/scenarios/{id}/predictions`.
- KPI cards: supply available (L), tankers available / total, heat alert level (0–3 badge), total predicted shortfall vs supply (as a "% of shortfall coverable" bar).
- Area table: name, population, last delivery (litres, relative time), requested litres, predicted need, expected piped, **predicted shortfall**, top-3 features (chips with ± litres). Sortable; shortfall descending by default.
- A model panel from `GET /a/v1/model`: XGBoost vs last-observed MAE and RMSE with the "simulation results only" disclaimer.
- A "Go to dispatch plan →" button calls `onOpenTab("plan")`.
- Emits `scenario_loaded` once per scenario id per mount, with the exact props in CONTRACT §7.

### Screen 2 — Dispatch plan (`DispatchPlanScreen`)
- When `plan` is null: an empty state with a primary "Generate plan" button (`onGeneratePlan`); a spinner while `generating`; `generateError` shown inline.
- **Header:** strategy, solver status badge (`time_limited` / `heuristic` in `--jp-warn`), solve time, total allocated / supply, total unmet in red, plan `status` pill. Then `actionsSlot` is rendered verbatim directly above the table.
- **Map:**
  - Leaflet: depot square, area circles sized by shortfall and coloured by coverage, one polyline per tanker in a distinct colour, stop-order labels
  - areas with unmet > 0 get a red ring
  - fetches `/scenarios/{plan.scenario_id}` for coordinates
  - automatic fallback to a schematic SVG (same nodes and edges on projected coordinates) after 3 tile errors, plus a manual Map/Schematic toggle
- **Ranked table:** rank, area, priority, shortfall, allocated, coverage %, tanker(s) and stop order, unmet (red when > 0), and a "Why?" expander showing `reason_text` and reason chips. Expanding fires `explanation_viewed`.
- **Tanker panel:** per tanker, ordered stops with litres, load / capacity, route km / max (the driver briefing, US-4).
- **Unserved panel:** always visible, red, listing `unserved[]` with the reason. If empty: "All predicted shortfall covered".
- Performance: renders a 10-area plan in under 300 ms after props arrive.

### Effort (story points, relative sizing; the same scale is used in MEMBER_B)

| ID | Module | SP |
|---|---|---|
| A1 | Synthetic generator, 3 seeds, training set, truth files, demo-property test | 5 |
| A2 | Scenario repository (local/S3) + scenario routes | 3 |
| A3 | Disruption application endpoint (§5.12) | 2 |
| A4 | ML: features, training, evaluation vs baseline, artefact, ModelInfo | 8 |
| A5 | Prediction service + route + SHAP top features + cache | 3 |
| A6 | S3 + SageMaker integration with local fallback | 5 |
| A7 | CP-SAT allocation model + pure constraint checks + fallback | 8 |
| A8 | Routing and sequencing + PlanResult assembly | 5 |
| A9 | Explanation engine (reason codes + templates) | 3 |
| A10 | JWT verification, error envelope, logging, request ids | 1 |
| A11 | Screen 1 Crisis snapshot | 3 |
| A12 | Screen 2 Dispatch plan (map + schematic fallback, table, tanker and unserved panels) | 8 |
| A13 | Standalone screen harness (fake shell, fixture/solve plan source) | 3 |
| A14 | Tests: unit, property, contract, e2e | 3 |
| A15 | Dockerfile + SAM template (S3, SageMaker role, Lambda container) | 3 |
| | **Total A** | **63** |

---

## 2. Ownership manifest

**A owns everything under `/member-a/**`**, specifically:

```text
member-a/
├── MEMBER_A.md                 # this file (from T0)
├── CLAUDE.md                   # §9 block, saved verbatim
├── README.md
├── CONTRACT_ISSUES.md
├── .gitignore  .dockerignore  .env.example  .env (gitignored)
├── backend/
│   ├── pyproject.toml          # package jp_a, python >=3.11,<3.13
│   ├── requirements.txt        # runtime pins (§3)
│   ├── requirements-dev.txt    # pytest etc.
│   ├── Dockerfile              # build context = /member-a
│   └── jp_a/
│       ├── __init__.py  main.py  config.py  logging_setup.py
│       ├── api/        deps.py auth.py errors.py schemas.py
│       │               routes_health.py routes_scenarios.py routes_predictions.py routes_plans.py routes_model.py
│       ├── services/   scenario_service.py disruption_service.py prediction_service.py plan_service.py explanation_service.py
│       ├── domain/     entities.py priority.py constraints.py reasons.py disruptions.py
│       ├── repositories/ scenario_repo.py model_repo.py storage_local.py storage_s3.py sagemaker_client.py
│       ├── ml/         features.py train.py evaluate.py
│       ├── optimization/ allocation.py routing.py solve.py
│       └── data/       generator.py cli.py
├── data/               scenarios/*.json  derived/*.json (gitignored)  training/*.csv  truth/*.json
├── models/             shortage_xgb_v1.json  model_meta.json
├── scripts/            generate_data.sh  train_model.sh  mint_dev_token.py  run_dev.sh  check_ownership.sh
├── tests/              unit/  property/  contract/  e2e/  fixtures/  conftest.py
├── dashboard/
│   ├── package.json  package-lock.json  tsconfig.json  vite.config.ts  index.html  .env.example
│   └── src/
│       ├── contract/types.ts          # verbatim CONTRACT §9.4
│       ├── screens/index.ts           # the mount module (exact exports §9.2)
│       ├── screens/SnapshotScreen.tsx  DispatchPlanScreen.tsx
│       ├── screens/components/*.tsx   # RouteMap, SchematicMap, PlanTable, TankerPanel, UnservedPanel, KpiCard…
│       ├── screens/api.ts  format.ts  *.module.css
│       └── harness/  main.tsx  HarnessShell.tsx  tokens.css  fixtures/contract_plan_record_example.json
└── infra/              template.yaml  samconfig.toml  lambda.Dockerfile  sagemaker/train_entry.py
```

| Resource type | A owns |
|---|---|
| Routes | `/a/v1/health`, `/a/v1/scenarios`, `/a/v1/scenarios/{scenario_id}`, `/a/v1/scenarios/{scenario_id}/disruptions`, `/a/v1/scenarios/{scenario_id}/predictions`, `/a/v1/plans/solve`, `/a/v1/model` |
| Storage | S3 bucket `jalpriority-a-${AWS::AccountId}` (prefixes `a/scenarios/`, `a/derived/`, `a/models/`, `a/training/`); local `/member-a/data/**`, `/member-a/models/**`. **No DB tables.** |
| UI | `SnapshotScreen`, `DispatchPlanScreen`, everything under `/member-a/dashboard` |
| Ports | 8001 (API), 5174 (harness) |
| Names | Python `jp_a`; npm `@jp/a-dashboard`; Docker image `jp-a-api`; SAM stack `jalpriority-a`; SageMaker jobs `jp-a-*`; CSS `jpa-`; storage `jp.a.*` (harness only); log `service: "jp-a"` |
| Events | Emits only `scenario_loaded` and `explanation_viewed`, via the `onTrack` prop |

### DO NOT TOUCH
- `/contracts/**` (read only)
- `/member-b/**` (do not read it for implementation hints either; build only from the contract)
- `/integration/**`
- any repo-root file; do not create one
- any `B_*`, `VITE_B_*` or new `SHARED_*` variable
- any `/b/*` route
- DynamoDB tables `jp_b_*`
- port 8002 or 5173
- the shell, login, Fairness, Compare and Replan screens; the ledger, baselines, metrics, approval and audit

---

## 3. Tech stack and dependencies

**Backend** (`/member-a/backend/requirements.txt`; if a pin fails to install on your platform, move to the nearest working patch release in this file only):
```text
fastapi==0.115.6
uvicorn[standard]==0.32.1
pydantic==2.10.4
pydantic-settings==2.7.0
numpy==2.1.3
pandas==2.2.3
scikit-learn==1.6.0
xgboost==2.1.3
ortools==9.11.4210
pyjwt==2.10.1
boto3==1.35.90
mangum==0.19.0
orjson==3.10.12
```
`requirements-dev.txt`: `pytest==8.3.4`, `httpx==0.28.1`, `hypothesis==6.123.2`, `jsonschema==4.23.0`, `ruff==0.8.4`.

**Frontend** (`/member-a/dashboard/package.json`, exact pins per CONTRACT §9.7): `react 18.3.1`, `react-dom 18.3.1`, `leaflet 1.9.4`, `react-leaflet 4.2.1`. Dev: `typescript 5.6.3`, `vite 5.4.11`, `@vitejs/plugin-react 4.3.4`, `@types/react 18.3.12`, `@types/react-dom 18.3.1`, `@types/leaflet 1.9.14`, `vitest 2.1.8`, `@testing-library/react 16.1.0`, `jsdom 25.0.1`. npm scripts: `dev` (harness on port 5174), `build`, `typecheck`, `test`.

**Env vars** (`/member-a/.env.example`):

| Var | Default | Purpose |
|---|---|---|
| `A_PORT` | `8001` | API port |
| `A_DATA_DIR` | `<member-a>/data` (in Docker: `/app/data`) | scenarios, training, truth |
| `A_MODELS_DIR` | `<member-a>/models` (in Docker: `/app/models`) | model artefact |
| `A_STORAGE_MODE` | `local` | `local` \| `s3` |
| `A_S3_BUCKET` | — | used when `s3` |
| `A_S3_PREFIX` | `a/` | |
| `A_MODEL_MODE` | `local` | `local` \| `s3` \| `sagemaker_endpoint` |
| `A_SAGEMAKER_ENDPOINT` | — | |
| `A_SAGEMAKER_ROLE_ARN` | — | |
| `A_AWS_REGION` | `ap-south-1` | |
| `A_SOLVER_TIME_LIMIT_S` | `8.0` | default when the request omits it |
| `A_COVERAGE_FLOOR` | `0.25` | φ |
| `A_FLOOR_PENALTY` | `3.0` | κ |
| `A_SEED` | `42` | generator seed |
| `A_AUTH_DISABLED` | `false` | local tests only |
| `A_CORS_ORIGINS` | `http://localhost:5174` | |
| `VITE_A_HARNESS_PLAN_SOURCE` | `solve` | harness only: `solve` \| `fixture` |
| `VITE_A_DEV_TOKEN` | — | harness only: JWT from `scripts/mint_dev_token.py` |

Under a `# SHARED (contract-owned, read-only)` heading, also list `SHARED_JWT_SECRET`, `SHARED_JWT_ISSUER`, `SHARED_JWT_AUDIENCE`, `SHARED_ENV` and `SHARED_LOG_LEVEL` with the CONTRACT §4.8 dev defaults.

---

## 4. Build order (milestones; commit after each one with the tests green)

| M | Deliverable | Done when |
|---|---|---|
| M0 | Scaffold `jp_a`: config, JSON logging, request-id middleware, error envelope (including re-wrapping 422s), JWT verify dependency, `/a/v1/health`, `scripts/mint_dev_token.py`, `scripts/check_ownership.sh` | `pytest tests/unit` passes; health returns 200; requests without a token get the 401 envelope |
| M1 | Strict Pydantic entities for every CONTRACT §5 model; the 9 contract fixtures copied in; the `contract/types.ts` byte-compare test | Fixture parsing tests are green |
| M2 | Generator + 3 seeds + training set + truth files; scenario repo (local); routes 6.2 and 6.3 | Determinism and demo-property tests are green |
| M3 | Train and evaluate the model, commit the artefact, ModelInfo route 6.7 | Metrics recorded in `model_meta.json` |
| M4 | Prediction service (SHAP top-3, cache, `run_mae_litres`), route 6.5 | Latency < 2 s; F-1 holds |
| M5 | `domain/priority.py` (F-2 to F-5) and `domain/constraints.py` (F-15) as pure functions with unit tests | The §5.11 worked check reproduces 1.4093 / 2.2144 / 2.6857 |
| M6 | CP-SAT allocation + greedy fallback | Solving the §5.3 example with the §6 SolveRequest gives `allocated = {ar_03: 6000, ar_02: 9000, ar_01: 5000}` (the same litres per area as §5.6) |
| M7 | Routing + PlanResult assembly + explanation engine; route 6.6 | The response equals the §5.6 example in every field except `result_id`, `generated_at` and timings. If tanker choice differs, the per-area litres, totals and F-15 must still match; log the difference in CONTRACT_ISSUES. |
| M8 | Disruption endpoint 6.4 | §5.12 tests: all three types, errors, hashing, idempotency |
| M9 | Property tests (50 random scenarios with 0 violations, time-limit behaviour at 0.5 s) + full contract suite + e2e | All green |
| M10 | Dashboard: types, api client, SnapshotScreen, DispatchPlanScreen, harness | `npm run typecheck && npm test && npm run build` pass; manual harness walk-through works |
| M11 | Dockerfile (context `/member-a`, copies `data/` and `models/`, `CMD uvicorn jp_a.main:app --host 0.0.0.0 --port ${A_PORT}`), README with setup, data disclosure, model results and limitations | `docker build` and run → health 200 |
| M12 (stretch, last) | `A_STORAGE_MODE=s3`, `A_MODEL_MODE=s3\|sagemaker_endpoint`, `infra/template.yaml` (S3 bucket, IAM least privilege, Lambda container via Mangum with 2048 MB memory and 15 s timeout, SageMaker execution role), `sagemaker/train_entry.py` reusing `ml/train.py` | Each AWS mode works, or is cleanly disabled with the local fallback still the default |

---

## 5. Mocks and stubs (for what A consumes from B)
A's backend consumes **nothing** from B. A's screens consume only the shell props (CONTRACT §9.4). Stub them with the **harness**:
- `src/harness/HarnessShell.tsx` imitates B's shell:
  - header with the synthetic badge
  - two tabs (Snapshot, Plan)
  - holds `scenarioId`, a fake `officerId = "officer_01"`, `role = "dispatcher"`
  - `token = VITE_A_DEV_TOKEN`
  - `onTrack` logs to the console and to an on-screen event list
  - `onOpenTab` switches tabs
  - `actionsSlot` = three disabled placeholder buttons labelled "Approve / Edit weights / Reject (B)"
- **Single flag** `VITE_A_HARNESS_PLAN_SOURCE`:
  - `solve`: `onGeneratePlan` calls A's own `POST /a/v1/plans/solve` with default weights and the debt derived from the scenario history (F-10, λ = 0.3, computed in the harness). It wraps the PlanResult in a PlanRecord (`plan_id` = `pln_` + 12 random hex, status `recommended`, metrics computed in the harness with F-9 and F-11 to F-15).
  - `fixture`: loads `fixtures/contract_plan_record_example.json`.
- The harness is never imported by `screens/`. At integration, B's shell replaces it entirely.

---

## 6. Contract tests (`/member-a/tests/contract/`)
1. **Fixtures:** all 9 CONTRACT §10 fixtures parse under strict (`extra="forbid"`) models.
2. **Live endpoints** via `TestClient` with a minted token:
   - 6.1 to 6.7 responses parse strictly; key sets equal the contract's exactly
   - IDs match the §4.2 regexes; datetimes end in `Z` with milliseconds
   - errors use the §4.5 envelope with the listed codes and status codes: 401 without a token, 401 with a wrong `iss`/`aud`, 403 with `role: "guest"`, 404 for an unknown scenario, 422 for bad weights, 409/422 for disruptions
3. **Formulas:** on every solve response, F-1 to F-8 recomputed independently in the test equal the response fields, and F-15 equals 0.
4. **Golden:** solving the §5.3 example gives the §5.6 per-area allocations and totals.
5. **Disruptions:** each type's effect matches §5.12 exactly, the derived id matches the regex and a recomputed hash, and repeating the call returns 200 with the same id.
6. **Frontend:** `src/contract/types.ts` equals CONTRACT §9.4 after whitespace normalisation (test reads `../../contracts/CONTRACT.md`). A type test assigns `SnapshotScreen` to `React.FC<SnapshotScreenProps>` and `DispatchPlanScreen` to `React.FC<DispatchPlanScreenProps>`. `screens/index.ts` exports exactly the 3 names. A vitest render test mounts both screens with the fixture PlanRecord and asserts that unserved rows have the danger class and that clicking "Why?" calls `onTrack` with the exact `explanation_viewed` shape.
7. **Isolation lint:** grep tests assert that `screens/**` contains no `"/b/"`, no `import.meta.env`, no `react-router`, and no `localStorage`/`sessionStorage`.

---

## 7. Definition of done
- [ ] All milestones M0–M11 complete. M12 either works or is explicitly marked "not deployed" in the README.
- [ ] `pytest` is fully green: unit, property, contract and e2e. `npm run typecheck && npm test && npm run build` are green.
- [ ] Every CONTRACT §6 endpoint is implemented with exact schemas, errors and auth.
- [ ] 0 constraint violations across the seeds and 50 random scenarios. A 10-area, 4-tanker solve takes under 10 s with the default limit, and under 4 s with `time_limit_s = 3.0`.
- [ ] The demo property holds on `scn_sa01_w06`.
- [ ] `scripts/check_ownership.sh` passes: `git diff --name-only t0...HEAD` lists only paths under `member-a/`.
- [ ] `docker build -f backend/Dockerfile .` (from `/member-a`) builds, and the container serves health on 8001.
- [ ] The README covers setup, data disclosure, model results vs baseline, optimizer formulation and limitations.
- [ ] `CONTRACT_ISSUES.md` exists, even if it only says "None".

---

## 8. Hand-off notes for merge
- Leave `A_AUTH_DISABLED=false` as the documented default.
- In the README, list how to run the API and the harness, and the exact path B's shell will alias: `member-a/dashboard/src/screens/index.ts`.
- Make sure `npm ci` in `/member-a/dashboard` works from a clean checkout. B's build resolves `leaflet` and `react-leaflet` from `member-a/dashboard/node_modules`.

---

## 9. Claude Code operating instructions — save verbatim as `/member-a/CLAUDE.md`

```markdown
# CLAUDE.md — Member A (JalPriority AI, "Need → Plan" slice)

You are building Member A's half of JalPriority AI. Your working directory is `member-a/`.
Sources of truth, in order: `../contracts/CONTRACT.md` (frozen), then `./MEMBER_A.md`.

## Authority
- You have full decision authority. Run everything end to end autonomously: scaffold, install, generate data, train, implement, test, fix, commit.
- Make every routine engineering decision yourself (library usage, file structure inside your folder, algorithms, UI layout details). Do not ask for confirmation.
- Ask the user ONLY when genuinely blocked: missing credentials for an AWS step, a hard platform failure you cannot work around, or a contradiction that makes the contract impossible to satisfy (log it first, see below).

## Boundaries (non-negotiable)
- Create, edit and delete files ONLY under `member-a/`. Never touch `../contracts/`, `../member-b/`, `../integration/` or any repo-root file.
- Never modify `../contracts/CONTRACT.md`. If it seems wrong, ambiguous or impossible, append an entry to `member-a/CONTRACT_ISSUES.md` (section number, problem, workaround in use, proposed fix) and keep building against the contract as written.
- Do not read `../member-b/` for hints. Build only from the contract.
- Only use env vars prefixed `A_` or `VITE_A_`, plus the listed `SHARED_*` read-only vars. Only serve routes under `/a/v1`. Never call `/b/*` from anything.
- Before every commit, run `scripts/check_ownership.sh` (fails if `git diff --name-only t0...HEAD` contains any path outside `member-a/`).

## How to work
- Follow the milestone order in MEMBER_A.md §4. After each milestone: run the tests, fix until green, then commit with message `A-M<n>: <summary>`.
- Contract tests are mandatory and must stay green. Strict models in tests (`extra="forbid"`), tolerant at runtime.
- Exact field names, ID formats, error codes and rounding come from the contract. Never invent or rename fields.
- Prefer the local fallback for every AWS service. AWS work (M12) is last and must never break the local default.
- Keep the solver within its time limit; always return a feasible plan with 0 violations.
- Python 3.11+, a venv at `member-a/.venv`, `pip install -e backend -r backend/requirements-dev.txt`. Node 20; `npm ci` in `member-a/dashboard`.
- When finished, verify the Definition of Done checklist in MEMBER_A.md §7 item by item and report the result.
```
