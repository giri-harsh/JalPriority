# INTEGRATION.md — merging A and B (JalPriority AI)

This is done **once, at the end**, by both humans together on `main`. Before this point, nobody touches `/integration/**` except this file, which is present from T0.

---

## 0. Pre-merge gate (each member, on their own branch)
- [ ] MEMBER_x.md §7 Definition of Done is all checked.
- [ ] `scripts/check_ownership.sh` passes on `a/main` and on `b/main`.
- [ ] Both `CONTRACT_ISSUES.md` files have been read aloud and triaged:
  - "no change needed"
  - "fix inside one member's folder"
  - "contract change": bump to 1.0.1, edit `/contracts/CONTRACT.md` on `main`, and both members' code is fixed on `main`
- [ ] Shared frontend pins are identical: `react`, `react-dom` and `@types/react` versions match in both `package-lock.json` files. Check with `npm ls react react-dom` in both dashboards; they must report the same versions.

---

## 1. Merge steps

### 1.1 Git
```bash
git checkout main            # main == t0
git merge --no-ff a/main     # only member-a/** changes, so no conflicts
git merge --no-ff b/main     # only member-b/** changes, so no conflicts
# Verify zero intersection
git diff --name-only t0 a/main | grep -v '^member-a/' && echo "A LEAK" || echo "A clean"
git diff --name-only t0 b/main | grep -v '^member-b/' && echo "B LEAK" || echo "B clean"
```

### 1.2 Create the integration files (exact list; §2 has their contents)
```text
integration/
├── INTEGRATION.md                 # this file (already present)
├── README.md                      # combined README
├── docker-compose.yml
├── .env.example
├── .env                           # gitignored; copied from .env.example
├── .gitignore                     # contains: .env
├── gateway/
│   ├── nginx.conf
│   ├── Dockerfile                 # builds the SPA (B shell + real A screens) and serves it via nginx
│   └── Dockerfile.dockerignore    # BuildKit per-Dockerfile ignore (build context is the repo root)
├── scripts/
│   ├── dev_local.sh               # run everything without Docker
│   ├── smoke.sh                   # curl health checks + login + one plan
│   └── demo_reset.sh              # wipe B's SQLite and A's derived scenarios for a clean demo
└── tests/
    ├── requirements.txt           # pytest==8.3.4, httpx==0.28.1
    └── test_cross_member_e2e.py   # automated §3 checklist (API level)
```

### 1.3 Swap the mocks for the real implementations

| Interface | Flag | Build value | Merge value |
|---|---|---|---|
| A API, as seen by B's backend | `B_USE_MOCK_A` | `true` | **`false`** |
| A API URL | `B_UPSTREAM_A_URL` | `http://localhost:8001` | `http://jp-a-api:8001` (Compose) or `http://localhost:8001` (local) |
| A screens, as seen by B's shell | `VITE_B_USE_MOCK_A_SCREENS` | `true` | **`false`** |
| A screens path | `VITE_B_A_SCREENS_PATH` | default | `../../member-a/dashboard/src/screens/index.ts` |
| A auth bypass | `A_AUTH_DISABLED` | `true` (A's tests only) | **`false`** |
| JWT secret, issuer and audience | `SHARED_JWT_*` | each member's dev copy | the **single** value in `/integration/.env`, read by both |

### 1.4 Wire env vars
Copy `/integration/.env.example` to `/integration/.env` and set a fresh `SHARED_JWT_SECRET` (`python -c "import secrets;print(secrets.token_urlsafe(48))"`). Compose passes the same file to both APIs.

### 1.5 Run the live contract tests first (fastest way to find drift)
```bash
# Terminal 1: A
cd member-a && source .venv/bin/activate && set -a && . ../integration/.env && set +a && uvicorn jp_a.main:app --port 8001
# Terminal 2: B's consumer contract suite against the real A
cd member-b && source .venv/bin/activate && set -a && . ../integration/.env && set +a && \
  B_USE_MOCK_A=false B_CONTRACT_LIVE_A=1 pytest tests/contract -q
# A's own suite
cd member-a && pytest tests/contract -q
```
Fix any failure before going further (see §4).

### 1.6 Bring up the full stack
```bash
cd integration && docker compose up --build
# open http://localhost:8080 , log in as officer_01 / jal-demo-2026
```
Alternatively, without Docker, run `integration/scripts/dev_local.sh`. It starts A on 8001, B on 8002 with `B_USE_MOCK_A=false`, and B's Vite on 5173 with `VITE_B_USE_MOCK_A_SCREENS=false`.

### 1.7 Run the automated and manual checks
```bash
cd integration && pip install -r tests/requirements.txt && JP_BASE_URL=http://localhost:8080 pytest tests -q
```
Then walk through the manual UI checklist (§3.2).

---

## 2. Integration file contents

### 2.1 `docker-compose.yml`
```yaml
name: jalpriority
services:
  jp-a-api:
    build:
      context: ../member-a
      dockerfile: backend/Dockerfile
    image: jp-a-api:local
    env_file: [.env]
    environment:
      A_PORT: "8001"
      A_AUTH_DISABLED: "false"
      SHARED_ENV: docker
    ports: ["8001:8001"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8001/a/v1/health')"]
      interval: 5s
      timeout: 3s
      retries: 20
  jp-b-api:
    build:
      context: ../member-b
      dockerfile: backend/Dockerfile
    image: jp-b-api:local
    env_file: [.env]
    environment:
      B_PORT: "8002"
      B_USE_MOCK_A: "false"
      B_UPSTREAM_A_URL: http://jp-a-api:8001
      B_SQLITE_PATH: /app/var/jp_b.sqlite3
      SHARED_ENV: docker
    volumes: ["b-var:/app/var"]
    ports: ["8002:8002"]
    depends_on:
      jp-a-api: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8002/b/v1/health')"]
      interval: 5s
      timeout: 3s
      retries: 20
  jp-gateway:
    build:
      context: ..
      dockerfile: integration/gateway/Dockerfile
    image: jp-gateway:local
    ports: ["8080:8080"]
    depends_on:
      jp-a-api: {condition: service_healthy}
      jp-b-api: {condition: service_healthy}
volumes:
  b-var: {}
```

### 2.2 `.env.example`
```dotenv
# SHARED (contract-owned)
SHARED_JWT_SECRET=REPLACE_WITH_48+_RANDOM_CHARS
SHARED_JWT_ISSUER=jalpriority-b
SHARED_JWT_AUDIENCE=jalpriority
SHARED_ENV=docker
SHARED_LOG_LEVEL=INFO
# A
A_STORAGE_MODE=local
A_MODEL_MODE=local
A_SOLVER_TIME_LIMIT_S=8.0
A_CORS_ORIGINS=http://localhost:8080
# B
B_STORE_MODE=sqlite
B_DEMO_USERS=officer_01:jal-demo-2026:dispatcher,admin_01:jal-admin-2026:admin
B_DEBT_DECAY=0.3
B_REPLAN_TIME_LIMIT_S=3.0
B_FF_FAIRNESS_WEIGHTS=true
B_FF_VULNERABILITY=true
B_FF_REPLAN=true
B_CORS_ORIGINS=http://localhost:8080
```

### 2.3 `gateway/nginx.conf`
```nginx
server {
  listen 8080;
  client_max_body_size 2m;
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_read_timeout 30s;

  location /a/ { proxy_pass http://jp-a-api:8001; }
  location /b/ { proxy_pass http://jp-b-api:8002; }

  location / {
    root /usr/share/nginx/html;
    try_files $uri /index.html;
  }
}
```
(`proxy_pass` without a URI keeps the full `/a/v1/...` and `/b/v1/...` paths, which is what both apps expect.)

### 2.4 `gateway/Dockerfile` (build context = repo root)
```dockerfile
FROM node:20-alpine AS build
WORKDIR /repo
COPY member-a/dashboard/package.json member-a/dashboard/package-lock.json member-a/dashboard/
RUN cd member-a/dashboard && npm ci
COPY member-b/dashboard/package.json member-b/dashboard/package-lock.json member-b/dashboard/
RUN cd member-b/dashboard && npm ci
COPY member-a/dashboard member-a/dashboard
COPY member-b/dashboard member-b/dashboard
COPY contracts contracts
ENV VITE_B_USE_MOCK_A_SCREENS=false \
    VITE_B_A_SCREENS_PATH=../../member-a/dashboard/src/screens/index.ts
RUN cd member-b/dashboard && npx vite build

FROM nginx:1.27-alpine
COPY integration/gateway/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/member-b/dashboard/dist /usr/share/nginx/html
```

### 2.5 `gateway/Dockerfile.dockerignore`
```text
**/node_modules
**/.venv
**/dist
**/__pycache__
**/.env
member-a/data/derived
member-b/var
.git
```

### 2.6 `scripts/dev_local.sh` (outline; implement exactly)
1. `set -a; . integration/.env; set +a`, then override `SHARED_ENV=local`.
2. Start A: `cd member-a && .venv/bin/uvicorn jp_a.main:app --port 8001 &`
3. Start B: `cd member-b && B_USE_MOCK_A=false B_UPSTREAM_A_URL=http://localhost:8001 .venv/bin/uvicorn jp_b.main:app --port 8002 &`
4. Start the shell: `cd member-b/dashboard && VITE_B_USE_MOCK_A_SCREENS=false npm run dev` (port 5173, proxies `/a` and `/b`).
5. Trap `EXIT` to kill the background processes.

### 2.7 `scripts/smoke.sh`
Uses curl with `BASE=${JP_BASE_URL:-http://localhost:8080}`:
1. health A and health B
2. login, then extract the token
3. `GET /a/v1/scenarios` with the user token (proves A accepts B's JWT)
4. `POST /b/v1/plans` for `scn_sa01_w06` (proves B → A works)
5. print the plan's `metrics`

### 2.8 `README.md` (combined)
Sections:
1. What JalPriority is: the PRD one-liner plus the synthetic-data disclosure
2. Architecture: two services, gateway, single SPA, and ownership (link to CONTRACT.md)
3. Quick start (Docker), plus local without Docker
4. Demo script (§3.2 condensed)
5. Model results: link to A's README, simulation-only disclaimer
6. Fairness policy and debt: link to B's README
7. Limitations and out-of-scope (PRD §4)
8. AWS (optional, §5)
9. Contract version and the list of resolved CONTRACT_ISSUES

---

## 3. End-to-end checklist (every cross-member flow in the PRD)

### 3.1 Automated (`tests/test_cross_member_e2e.py`, API level through the gateway)

| # | Flow | Assertion |
|---|---|---|
| E1 | Auth crosses the boundary | Login on B → the token works on `GET /a/v1/scenarios` (200). No token → 401 envelope from A. Tampered token → 401. |
| E2 | Scenario passthrough | `GET /b/v1/scenarios/scn_sa01_w06` deep-equals `GET /a/v1/scenarios/scn_sa01_w06` |
| E3 | Plan creation (US-3, US-4) | `POST /b/v1/plans` → 201; `result.strategy == "jalpriority"`; `metrics.constraint_violations == 0`; `result.constraint_check.violations == []`; wall time < 10 s |
| E4 | Debt reaches the optimizer | Every `allocation.debt_litres_used` equals `GET /b/v1/ledger` `current_debt_litres` for that area (window 5) |
| E5 | Priority formula agreement | B's recomputation of F-5 equals A's `priority_weight` (±0.0001) for all areas |
| E6 | Metrics agreement | `metrics.total_unmet_litres == result.totals.unmet_litres`; `metrics.total_travel_km == result.totals.travel_km` |
| E7 | Weights change the plan (US-7) | PUT weights with `debt = 0`, then regenerate → the plan differs from E3, or the priorities differ; a `weights_changed` event is stored |
| E8 | Vulnerability flag | Restart B with `B_FF_VULNERABILITY=false` → all `vulnerability_used` = 0 in A's result (optional; may be manual) |
| E9 | Approve → ledger (US-10, US-6) | Approve the E3 plan → the ledger has window 6 rows with `source: "approved_plan"`, and their values equal F-10 of the window-5 debt and the plan's unmet |
| E10 | Edit → supersede | Decision `edited` with revised weights → `next_plan.supersedes_plan_id == old.plan_id`; old status `edited` |
| E11 | Reject | A new plan rejected with a note → status `rejected`; a second decision → 409 |
| E12 | Comparison (US-9) | `POST /b/v1/comparisons` → 3 results with the same scenario and supply. On `scn_sa01_w06`, JalPriority PWU < FCFS PWU and < nearest-first PWU; JalPriority min coverage ≥ both; violations 0 for all |
| E13 | Replan: tanker down (US-8) | `POST /b/v1/replans` with an available tanker → the derived id matches the regex; the new plan has no deliveries from that tanker; `replanning_time_ms < 5000`; diff non-empty; the old plan is `superseded` |
| E14 | Replan: supply cut | fraction 0.3 → `totals.supply_available_litres == floor(S·0.7/100)·100`; total unmet increases |
| E15 | Replan: heat alert | level +1 → the derived scenario's `heat.alert_level` is +1 and predictions change |
| E16 | Disruption errors pass through | A tanker that is already down → B returns 502 `UPSTREAM_A_ERROR` with `details.upstream.code == "DISRUPTION_NOT_APPLICABLE"` |
| E17 | Events complete | After E1–E15, `GET /b/v1/events` contains every one of the 9 event names with the exact §7 props keys (the two A-emitted ones come from §3.2 steps U2 and U4; the automated test posts them through `/b/v1/events` as the shell would) |
| E18 | Audit trail | `GET /b/v1/audit` has `plan_generated`, `plan_approved`, `plan_edited`, `plan_rejected`, `weights_changed` and `replan_applied` |
| E19 | Time-limited solve | `POST /b/v1/plans` with `time_limit_s: 0.5` → `solver.status` ∈ {`time_limited`, `optimal`, `heuristic`}; violations 0 |
| E20 | Request id | Sending `X-Request-ID: req_000000000abc` to B → it appears in A's logs for the downstream call |

### 3.2 Manual UI walk-through (the PRD demo script, at http://localhost:8080)

| # | Step | Expect |
|---|---|---|
| U1 | Log in as officer_01 | Shell header with the synthetic badge; lands on Crisis snapshot (**real A screen**, with the Leaflet-era styling, not the mock table) |
| U2 | Pick `scn_sa01_w06` | KPIs, area table, top features and model panel load; a `scenario_loaded` event appears in `/b/v1/events` |
| U3 | Dispatch plan → Generate | Map with tanker routes and red rings on unserved areas, ranked table, `actionsSlot` (B's ApprovalBar) rendered above the table; under 10 s |
| U4 | Click "Why?" on an unserved area | Reason text names the cause; `explanation_viewed` stored |
| U5 | Compare | FCFS and nearest-first leave the far, vulnerable areas near 0%; JalPriority does not; 7 metrics side by side |
| U6 | Fairness & debt → set debt weight to 0 → Save & regenerate | Plan tab shows changed allocations |
| U7 | Restore weights, regenerate, Approve | Debt chart gains window 6 |
| U8 | Replan → tanker down → Apply | Time under 5 s, diff shows moved areas, "Open revised plan" shows the new map |
| U9 | Approve the revised plan | Window 6 debt rows replaced (not duplicated) |
| U10 | Kill A (`docker compose stop jp-a-api`) and generate | Clean inline error (502 `UPSTREAM_A_UNAVAILABLE`), no crash; restart A, retry works |
| U11 | Narrow the browser to 1280 px and 1024 px | Both A and B screens render inside the slot without horizontal overflow or style bleed |

---

## 4. Likely failure points and fixes

| Symptom | Likely cause | Fix (who) |
|---|---|---|
| "Invalid hook call" or a blank page after enabling real A screens | Two React copies (A's `node_modules/react` plus B's) | Confirm B's `resolve.dedupe` includes `react`, `react-dom` and `react/jsx-runtime`; confirm identical React versions (§0); delete both `node_modules` and re-run `npm ci`. (B config) |
| Vite: "The request url … is outside of Vite serving allow list" | `server.fs.allow` is missing the A path | Add `"../../member-a/dashboard"` to `fs.allow`. (B config) |
| "Failed to resolve import react-leaflet" during the gateway build | A's `node_modules` was not installed in the build stage | Keep the `npm ci` for `member-a/dashboard` in `gateway/Dockerfile` (integration) |
| Map shows broken marker images | Default Leaflet `Marker` icons used | Use `CircleMarker` only, per CONTRACT A3. (A) |
| A screens look unstyled or B screens change style after mounting A | Global CSS bleed, or tokens missing | A uses CSS Modules or `jpa-`; B defines `--jp-*` on `:root`; only `leaflet.css` is global. (owner of the offending CSS) |
| 401 on every `/a/v1` call with a valid login | Secret, `iss` or `aud` mismatch; A still reading its own dev `.env`; clock skew | Both services read `SHARED_JWT_*` from `/integration/.env` only; ensure no member `.env` overrides inside the containers; 30 s leeway. (integration) |
| 403 from A for B's backend calls | A's allowed roles are missing `service` | A allows `dispatcher`, `admin` and `service` (CONTRACT §8). (A) |
| B 502 `UPSTREAM_A_UNAVAILABLE` in Compose | Wrong `B_UPSTREAM_A_URL` (localhost inside a container) | `http://jp-a-api:8001`. (integration) |
| Strict model failure: "extra fields not permitted" | Field drift between A's output and the contract | Fix the producer to match CONTRACT §5; if the contract is wrong, bump it to 1.0.1 and fix both. Runtime parsing is tolerant, so production keeps working meanwhile. |
| Priority mismatch in E5 at the 4th decimal | Rounding inputs before computing p | Compute F-5 from unrounded U, D̂ and V, then round once. (whichever side deviates) |
| Debt off by one window (E4 or E9) | Using window w's debt instead of w−1, or seeding including the current window | Debt input = after window `window_index − 1`; seed windows `< window_index` only. (B) |
| JalPriority does not beat the baselines on `scn_sa01_w06` | The seed lacks the demo property, or the weights are too low | A's demo-property test must pass. Check the default weights; raise `A_COVERAGE_FLOOR` only if it is documented. (A) |
| Replan > 5 s | B not sending `time_limit_s = 3.0`, or A ignoring it, or a cold model load | B sends `B_REPLAN_TIME_LIMIT_S`; A respects `time_limit_s + 1`; A loads the model at startup. |
| The derived scenario id from B's mock differs from A's | Hashing input not canonical | Both use `json.dumps([...], sort_keys=True, separators=(",",":"))` over `{type, params}` only (CONTRACT §5.12). |
| Mock-era UI assumption breaks (for example, the mock always used `engine: "mock"`) | B's UI depended on mock-only values | B treats `engine`, `status` and timings as display-only strings. (B) |
| OR-Tools or XGBoost wheels fail on Apple Silicon Docker or Lambda | Architecture mismatch | Build with `--platform linux/amd64` or use arm64 wheels consistently; for Lambda, use the container image with a matching architecture. (A) |
| CORS errors | Calling APIs cross-origin instead of through a proxy | The browser always uses relative `/a` and `/b` through the Vite proxy or the gateway. CORS env vars are only for direct debugging. |
| Port already in use | A leftover dev server | `lsof -i :8001,8002,5173,5174,8080` and kill it. |

---

## 5. Optional AWS final setup (only after §3 passes locally)
1. A: `cd member-a/infra && sam build && sam deploy` creates stack `jalpriority-a` (S3, Lambda container, HTTP API). Note `AApiUrl`.
2. B: `cd member-b/infra && sam deploy --parameter-overrides UpstreamAUrl=<AApiUrl>` creates stack `jalpriority-b` (DynamoDB tables, Lambda, HTTP API). Note `BApiUrl`.
3. Set the same `SHARED_JWT_SECRET` in both stacks (SSM SecureString parameter `/jalpriority/shared/jwt_secret`, read by both). Creating the parameter is an integration step: `aws ssm put-parameter …`.
4. Frontend: build as in `gateway/Dockerfile`. Then either keep the nginx gateway on one host with `/a/` and `/b/` proxied to `AApiUrl` and `BApiUrl` (simplest), or create `/integration/aws/cloudfront.yaml` (S3 static site plus behaviours `/a/*` → A API and `/b/*` → B API). Only do the latter if time allows.
5. Re-run `tests/test_cross_member_e2e.py` with `JP_BASE_URL` set to the deployed URL.

If any AWS step slips, demo from Docker Compose. The PRD treats AWS as add-on, and local is the supported fallback for every service.
