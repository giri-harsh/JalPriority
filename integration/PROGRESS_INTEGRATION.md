# PROGRESS_INTEGRATION.md — JalPriority merge (resume file: read this + `git log --oneline -15` first)

## Status: PHASE 1 (local merge) GREEN
| Step | State |
|---|---|
| 0 clone her repo (b/main@7b55395), contract identical to t0 | done |
| 0.2 rounding fix on a/main (ceil cap, CI-7) | done, pushed (eb44d72) |
| 1 branch `integration` = main + a/main + member-b import | done |
| 2 gate: React pins identical; CI decisions in integration/README.md §9 | done |
| 3 integration files (compose, env, gateway, scripts ps1+sh, e2e, README) | done |
| 4 live contract tests (B vs real A 16/16; A 320/320) | done |
| 5 local full run: E1-E20 19 pass + E8 manual; demo_flow.py PASS; real A screens in bundle | done |
| 6 all suites green, pushed `integration` | done |

## Decisions
- Only member-b imported from her b/main; her repo untouched.
- B fixes (member-b only): UtcDatetime serialiser, X-Request-ID forwarding, FORBIDDEN role code.
- Docker/gateway written, NOT built here (Phase 2).

## Next 3 actions (Phase 2)
1. Verify docker compose build on a Docker host (EC2): A, B images, gateway.
2. AWS: EC2 + Docker + S3 (A storage/models) + DynamoDB (B store) with SHARED_JWT_SECRET in SSM.
3. Re-run integration/tests with JP_BASE_URL=<deployed URL>; manual E8.
