# PROGRESS.md — Member A (resume file: read this + `git log --oneline -15` first)

_Last updated: M0/M1 done by LEAD; wave-1 agents (DATA, ML, OPT, UI) about to launch._

## Milestones (MEMBER_A §4)
| M | Deliverable | Owner | Status |
|---|---|---|---|
| M0 | Scaffold: config, JSON logs, request-id, error envelope, JWT, health, token script, ownership script | LEAD | ✅ |
| M1 | Strict entities, 9 fixtures, types.ts byte-compare | LEAD | ✅ |
| M2 | Generator + 3 seeds + training set + truth + scenario repo + routes 6.2/6.3 | DATA | ⏳ |
| M3 | Train/evaluate model, artefact, ModelInfo 6.7 | ML | ⏳ |
| M4 | Prediction service (SHAP top-3, cache, run_mae), route 6.5 | ML | ⏳ |
| M5 | priority.py (F-2..F-5) + constraints.py (F-15) | OPT | ⏳ |
| M6 | CP-SAT allocation + greedy fallback (golden solve) | OPT | ⏳ |
| M7 | Routing + PlanResult assembly + explanations; route 6.6 | OPT | ⏳ |
| M8 | Disruption endpoint 6.4 | LEAD | 🟡 code done, tests pending |
| M9 | Property + full contract suite + e2e | QA | ⏳ |
| M10 | Dashboard (types, api, screens, harness) | UI | ⏳ |
| M11 | Dockerfile + README | QA / LEAD | ⏳ |
| M12 | AWS stretch (S3 / SageMaker / SAM) | QA | ⏳ (last) |

## Agents (file ownership — never cross)
- DATA: `jp_a/data/**`, `data/**`, `scripts/generate_data.sh`, `repositories/scenario_repo.py`, `repositories/storage_local.py`, `tests/unit/test_data_*.py`
- ML: `jp_a/ml/**`, `models/**`, `scripts/train_model.sh`, `repositories/model_repo.py`, `services/prediction_service.py`, `api/routes_predictions.py`, `api/routes_model.py`, `tests/unit/test_ml_*.py`
- OPT: `jp_a/optimization/**`, `domain/priority.py|constraints.py|reasons.py`, `services/plan_service.py|explanation_service.py`, `api/routes_plans.py`, `tests/unit/test_opt_*.py`
- UI: `dashboard/**` (except `src/contract/types.ts` = verbatim §9.4, LEAD-owned)
- QA: `tests/contract/**` (except LEAD's `test_fixtures.py`, `test_types_ts.py`), `tests/property/**`, `tests/e2e/**`, `backend/Dockerfile`, `infra/**`
- LEAD: scaffold, config, main.py, `api/deps|auth|errors|middleware|schemas`, `domain/entities|disruptions`, `services/scenario_service.py`, `api/routes_scenarios|health`, `tests/conftest.py`, `tests/fixtures/**`, README, CONTRACT_ISSUES.md, PROGRESS.md

## Environment decisions
- Python 3.12.13 venv at `member-a/.venv` via `uv venv -p 3.12` (system default is 3.14, outside `>=3.11,<3.13`). All pinned backend deps installed unchanged.
- Windows host; scripts run under Git Bash. `git config core.autocrlf input` + `member-a/.gitattributes` (LF) so shell scripts / byte-identical data never get CRLF.
- Docker not installed on this host → `docker build` check skipped (will be noted in README/STATUS).
- Node v24 (spec says 20); frontend pins kept exact per CONTRACT §9.7.
- No Claude attribution in commits: local `commit-msg` hook + `member-a/.claude/settings.json`.

## Last green test run
`pytest tests/unit tests/contract` → 75 passed (M0+M1).

## Known failures
none

## Next 3 actions
1. Commit M0/M1; launch wave 1 workflow (DATA → ML-train chain, OPT, UI in parallel).
2. LEAD: write `tests/unit/test_disruptions.py` while agents run; wip-commit snapshots every ~20 min.
3. After wave 1: integrate, run full suite, launch wave 2 (QA contract/property/e2e + Dockerfile + review loop).
