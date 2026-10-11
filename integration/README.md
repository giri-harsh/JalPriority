# JalPriority AI — integration (A + B)

**SYNTHETIC DATA · SIMULATION RESULTS.** All scenarios, the training set and the model metrics come from a seeded simulator; nothing here is real-world accuracy.

## 1. What JalPriority is
When tanker water is scarce, "first come, first served" and "closest first" abandon the far, vulnerable neighbourhoods that ask quietly. JalPriority predicts each ward's need (XGBoost), then plans litres, tankers and stop order with a fairness-aware CP-SAT optimizer that weighs urgency, accumulated deprivation debt and vulnerability, explains every allocation in plain language, compares itself with FCFS and nearest-first, supports disruption replanning (< 5 s) and keeps a human approval + audit trail.

## 2. Architecture
```
 browser ──▶ gateway (nginx :8080) or Vite dev (:5173, proxies /a and /b)
               ├── /          SPA: B's shell + A's real screens (Crisis snapshot, Dispatch plan)
               ├── /b/v1/*    jp-b-api :8002  plans, ledger, baselines, metrics, replan, approval, auth, events, audit (SQLite | DynamoDB)
               └── /a/v1/*    jp-a-api :8001  scenarios, predictions, optimizer, explanations, disruptions (local files | S3)
 B → A over HTTP only (service JWT). A never calls B.
```
Ownership: `member-a/**` = A, `member-b/**` = B, `integration/**` = this folder, `contracts/CONTRACT.md` = frozen v1.0.0 (unchanged by the merge; B's copy is byte-identical to `t0`).

## 3. Quick start
Without Docker (Windows PowerShell; Docker is optional and verified in Phase 2):
```powershell
cd integration
copy .env.example .env        # then set a fresh SHARED_JWT_SECRET:  python -c "import secrets;print(secrets.token_urlsafe(48))"
.\scripts\dev_local.ps1       # A :8001, B :8002 (B_USE_MOCK_A=false), Vite shell :5173 (real A screens)
# open http://localhost:5173  -> login officer_01 / jal-demo-2026
$env:JP_BASE_URL="http://localhost:5173"; python -m pytest tests -q      # E1-E20
python scripts\demo_flow.py                                              # scripted demo + headline numbers
.\scripts\demo_reset.ps1                                                 # clean DB / derived scenarios (stack stopped)
```
Prerequisites: `member-a/.venv` and `member-b/.venv` (Python 3.12, `uv venv -p 3.12 .venv` + `uv pip install -r backend/requirements.txt -r backend/requirements-dev.txt` + `uv pip install -e backend --no-deps`), `npm ci` in both dashboards. `dev_local.sh` is the bash twin.
Docker: `cd integration && docker compose up --build` then http://localhost:8080 (written, **not built on this machine**).

## 4. Demo script (condensed)
1. Login `officer_01` → Crisis snapshot (real A screen) → `scn_sa01_w06`: supply 24,400 L vs predicted shortfall 43,125 L (1.77×), heat alert 2.
2. Dispatch plan → Generate: map, ranked table, "Why?" reasons; far vulnerable wards ar_07, ar_03 served in full.
3. Compare: priority-weighted unmet JalPriority 29,192.7 vs FCFS 64,986.0 vs nearest-first 62,604.6 (min coverage 0.242 vs 0.000 / 0.000).
4. Fairness & debt: set debt weight 0 → save & regenerate → plan changes; restore; Approve → ledger window 6.
5. Replan → tanker_down tk_01 → < 5 s (2.0 s measured) → diff → approve the revised plan.

## 5. Model results
XGBoost need model, test MAE 883.6 L / RMSE 1,166.2 L vs last-observed baseline 1,161.7 / 1,523.4 (synthetic, simulation only). Details: `member-a/README.md`, `member-a/SUMMARY_A.md`.

## 6. Fairness policy and debt
`D' = round((1 − λ)·D + unmet)`, λ = 0.3, optional cap; priority `p = 1 + α·U + β·D̂ + γ·V`. Details: `member-b/README.md`.

## 7. Limitations / out of scope
One service area (8 wards, 4 tankers), one multi-stop trip per tanker per 12-hour window, edit = adjust weights and regenerate, template explanations (no LLM), synthetic data. Docker/gateway and AWS are not verified in Phase 1.

## 8. AWS
Not deployed in Phase 1 (planned Phase 2: EC2 + Docker + S3 + DynamoDB). See INTEGRATION.md §5.

## 9. Contract version and issue decisions (merge gate)
Contract v1.0.0, **no bump**. B's `contracts/CONTRACT.md` is byte-identical to `t0` (line-ending normalised), so `t0` wins trivially.

React pins are identical: `react@18.3.1`, `react-dom@18.3.1`, `@types/react@18.3.12`, `@types/react-dom@18.3.1` (`npm ls` shows one copy in each dashboard).

| Issue | Decision |
|---|---|
| A CI-1 `HEAT_ALERT` fires only when no debt/vulnerability/shortfall reason explains the area | **no change** (matches the §5.6 example; B treats reasons as display data) |
| A CI-2 `NOT_FOUND` / `METHOD_NOT_ALLOWED` codes outside §4.5 | **no change** (no contract route can trigger them) |
| A CI-3 `supply_cut` float vs exact decimal | **no change needed in B**: B's mock already uses `Decimal(str(f))`; verified live (E14 uses f = 0.3; A tests pin f = 0.05/0.3/0.5/0.9) |
| A CI-4 label of derived scenarios | **no change** (informational) |
| A CI-5 derived id must have exactly one `__d<k>_<hex6>` | **no change**: B's hash is identical (`json.dumps(..., sort_keys=True, separators=(",",":"))`, sha1[:6]); E13/E14/E15 recompute it |
| A CI-6 `weights` required, per-field defaults | **no change** (B always sends all four) |
| A CI-7 allocation cap is `ceil(sf/100)` units (≤ 99 L over shortfall, no rounding residue in `unserved[]`) | **fix in A (done on a/main)**; B does not assume `allocated ≤ shortfall` |
| B CONTRACT_ISSUES: "None" | — |

### Fixes made during integration (all inside `member-b/`, A needed none)
| File | Reason |
|---|---|
| `member-b/backend/jp_b/api/schemas.py` | datetimes serialised as `…:00Z` (no milliseconds) → violated CONTRACT 4.3 and broke the strict scenario passthrough (E2). Added `UtcDatetime` (UTC, ms, `Z`). |
| `member-b/backend/jp_b/request_context.py`, `main.py`, `clients/a_http.py` | B did not forward `X-Request-ID` to A (CONTRACT 4.7, E20). Contextvar set in the middleware, read by the A client. |
| `member-b/backend/jp_b/api/deps.py` | a wrong role answered 403 `UNAUTHORIZED`; the contract code is `FORBIDDEN`. |

### Known / open
* E8 (vulnerability feature flag off) is manual: restart B with `B_FF_VULNERABILITY=false`, then confirm `vulnerability_used = 0`.
* Docker images and the nginx gateway are written but unverified here (Phase 2).
