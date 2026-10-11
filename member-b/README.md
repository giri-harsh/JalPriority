# JalPriority AI — Member B ("Fairness, Evidence & Control" slice)

This service implements the Member B component of JalPriority AI, providing multi-objective fairness weighting, cumulative deprivation debt tracking, baseline heuristic comparison (FCFS, Nearest-First), disruption replanning with delta diff, human decision workflows, audit logging, and the unified dashboard shell.

---

## 1. Setup & Local Development

### Prerequisites
- **Python**: `>=3.11,<3.13` (tested on Python 3.12 / 3.13)
- **Node.js**: Node 20.x with `npm`
- **Docker**: Docker Engine / Docker Desktop (optional for containerized runs)

### Backend Setup
1. Create and activate a Python virtual environment:
   ```bash
   cd member-b
   python -m venv .venv
   # Windows PowerShell:
   .\.venv\Scripts\Activate.ps1
   # Linux/macOS:
   source .venv/bin/activate
   ```
2. Install package in editable mode with development dependencies:
   ```bash
   pip install -e backend -r backend/requirements-dev.txt
   ```
3. Run tests:
   ```bash
   pytest
   ```
4. Start the backend API server on port 8002:
   ```bash
   uvicorn jp_b.main:app --host 0.0.0.0 --port 8002 --reload
   ```

### Dashboard Setup
1. Install dependencies:
   ```bash
   cd member-b/dashboard
   npm ci
   ```
2. Run typecheck and tests:
   ```bash
   npm run typecheck
   npm test
   ```
3. Start the dashboard dev server on port 5173:
   ```bash
   npm run dev
   ```
4. Build for production:
   ```bash
   npm run build
   ```

### Docker
To build and run the container:
```bash
docker build -f backend/Dockerfile -t jp-b-api:local .
docker run -d -p 8002:8002 -v jp-b-var:/app/var -e B_USE_MOCK_A=true -e SHARED_JWT_SECRET=dev-only-jalpriority-secret-change-me-0123456789 jp-b-api:local
```

Real Docker build, health check, login, and plan creation verified on container (port 8002).

---

## 2. Environment Variables & Flags

Configuration is managed via environment variables (or `.env` in `member-b/`):

| Variable | Default | Purpose |
|---|---|---|
| `B_PORT` | `8002` | HTTP port for the FastAPI service |
| `B_STORE_MODE` | `sqlite` | Storage engine: `sqlite` (default) or `dynamodb` |
| `B_SQLITE_PATH` | `<member-b>/var/jp_b.sqlite3` | SQLite database file location (in Docker: `/app/var/jp_b.sqlite3`) |
| `B_DDB_TABLE_PREFIX` | `jp_b_` | Table name prefix for DynamoDB mode |
| `B_AWS_REGION` | `ap-south-1` | AWS region for DynamoDB and CloudWatch |
| `B_UPSTREAM_A_URL` | `http://localhost:8001` | Real Member A service URL |
| `B_USE_MOCK_A` | `true` | Swap flag for Member A API. When `true`, uses internal mock A (`jp_b/clients/a_mock.py`). |
| `B_A_TIMEOUT_S` | `15` | Timeout in seconds for upstream calls to Member A |
| `B_REPLAN_TIME_LIMIT_S` | `3.0` | Optimizer time limit sent on disruption replans |
| `B_DEMO_USERS` | `officer_01:jal-demo-2026:dispatcher,admin_01:jal-admin-2026:admin` | Comma-separated credentials (`user:password:role`) for authentication |
| `B_DEBT_DECAY` | `0.3` | Decay parameter λ for deprivation debt calculation |
| `B_DEBT_CAP_LITRES` | *(empty)* | Optional ceiling on cumulative deprivation debt per ward |
| `B_FF_FAIRNESS_WEIGHTS` | `true` | Feature flag: when `false`, custom weights are ignored and defaults are enforced |
| `B_FF_VULNERABILITY` | `true` | Feature flag: when `false`, vulnerability weight is forced to 0.0 and slider is disabled |
| `B_FF_REPLAN` | `true` | Feature flag: when `false`, `/b/v1/replans` returns 403 `FEATURE_DISABLED` |
| `B_CORS_ORIGINS` | `http://localhost:5173` | Allowed CORS origins for browser dashboard |
| `B_CONTRACT_LIVE_A` | `0` | `1` = runs consumer contract tests against a real live Member A service |
| `VITE_B_USE_MOCK_A_SCREENS` | `true` | Swap flag for Member A frontend screens. When `true`, mounts `src/mocks/aScreens.tsx`. |
| `VITE_B_A_SCREENS_PATH` | `../../member-a/dashboard/src/screens/index.ts` | Path to real Member A screens when `VITE_B_USE_MOCK_A_SCREENS=false` |

### Shared Contract Variables (read-only dev defaults)
- `SHARED_JWT_SECRET`: `dev-only-jalpriority-secret-change-me-0123456789`
- `SHARED_JWT_ISSUER`: `jalpriority-b`
- `SHARED_JWT_AUDIENCE`: `jalpriority`
- `SHARED_ENV`: `local`
- `SHARED_LOG_LEVEL`: `INFO`

---

## 3. Deprivation Debt Policy

Deprivation debt tracks persistent unserved water deficits across consecutive dispatch windows:
- **Formula (CONTRACT §5.11 F-10)**:
  `D_i' = round((1 − λ) · D_i + u_i)`
  where:
  - `D_i` is existing debt of area `i`
  - `λ` is `B_DEBT_DECAY` (default `0.3`)
  - `u_i = max(0, shortfall_i − allocated_i)` is unserved shortfall in the current dispatch window
- **Ceiling**: If `B_DEBT_CAP_LITRES` is configured, `D_i' = min(D_i', cap)`.
- **Ledger Lifecycle**:
  - *Seeding*: On initial encounter with a service area, historical windows 1 through `current_window - 1` are seeded using historical delivery logs.
  - *Approval Update*: When an officer approves a plan, the ledger rows for that window are idempotently updated or replaced.

---

## 4. End-to-End Demo Script

Follow this workflow to verify the end-to-end capabilities with `B_USE_MOCK_A=true` and `VITE_B_USE_MOCK_A_SCREENS=true`:

1. **Login**: Navigate to `http://localhost:5173/login`, sign in with `officer_01` and password `jal-demo-2026`.
2. **Crisis Snapshot**: Select scenario `scn_sa01_w06`. Review water supply shortfall, weather alerts, and tanker fleet availability.
3. **Generate Plan**: Switch to the **Dispatch plan** tab and click **Generate Plan** to solve the baseline JalPriority allocation.
4. **Compare Heuristics**: Navigate to the **Compare** tab. Observe how standard heuristics (FCFS and Nearest-First) leave at least one vulnerable ward completely unserved (0% coverage), whereas JalPriority AI balances coverage and minimizes deprivation debt.
5. **Adjust Fairness Policy**: Navigate to the **Fairness & debt** tab. Increase the debt weight (β) or vulnerability weight (γ), observe the live priority formula update, and click **Save & regenerate plan**.
6. **Approve Plan**: Return to the **Dispatch plan** tab, click **Approve**, and confirm the action. The plan transitions to `approved` and window 6 is committed to the ledger.
7. **Simulate Disruption**: Navigate to the **Replan** tab. Select `tanker_down` with tanker `tk_01` (vehicle breakdown) and click **Apply & replan**.
8. **Review Diff & Time**: Confirm the replanning latency readout is under 5 seconds (< 5,000 ms), review the metrics shift, and examine the colour-coded allocation diff.
9. **Approve Revised Plan**: Approve the revised plan directly using the embedded ApprovalBar. The ledger replaces window 6 rows with the newly approved plan.

---

## 5. Limitations

- **Synthetic Data**: All scenarios, telemetry, and demand models operate on synthetic municipal simulation data (`is_synthetic: true`).
- **Single-Zone Dispatch**: In current release, multi-depot routing optimizes within designated service areas independently.
- **In-Memory Mock Fallback**: When `B_USE_MOCK_A=true`, Member A endpoints are serviced by local heuristic and greedy solver replicas without external neural network inference.

---

## 6. AWS DynamoDB Deployment Status

DynamoDB storage mode implemented and tested with moto. Deployment: at integration (EC2 + DynamoDB). M14: not deployed by Member B.
*(Local SQLite remains the default active repository engine).*

---

## 7. Mock Switching for Merge & Production

To transition from mocked development mode to live integration:

1. **Switch Backend API to real Member A**:
   In `member-b/.env` (or environment):
   ```env
   B_USE_MOCK_A=false
   B_UPSTREAM_A_URL=http://localhost:8001
   ```
2. **Switch Frontend Screens to real Member A package**:
   In `member-b/dashboard/.env`:
   ```env
   VITE_B_USE_MOCK_A_SCREENS=false
   VITE_B_A_SCREENS_PATH=../../member-a/dashboard/src/screens/index.ts
   ```
3. Run consumer contract tests against the live service:
   ```bash
   B_CONTRACT_LIVE_A=1 pytest tests/contract/test_mock_conformance.py
   ```
