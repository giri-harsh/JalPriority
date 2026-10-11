#!/usr/bin/env bash
# Smoke test (INTEGRATION.md 2.7). BASE defaults to the gateway; use http://localhost:5173 with dev_local.
set -eu
BASE="${JP_BASE_URL:-http://localhost:8080}"
need() { command -v "$1" >/dev/null || { echo "missing $1" >&2; exit 2; }; }
need curl; need python

echo "health A: $(curl -fsS "$BASE/a/v1/health")"
echo "health B: $(curl -fsS "$BASE/b/v1/health")"
TOKEN=$(curl -fsS -X POST "$BASE/b/v1/auth/login" -H 'Content-Type: application/json' \
  -d '{"username":"officer_01","password":"jal-demo-2026"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
[ -n "$TOKEN" ] && echo "login ok"
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/a/v1/scenarios" | python -c 'import sys,json;d=json.load(sys.stdin);print("A accepted B token; scenarios:",len(d["scenarios"]))'
curl -fsS -X POST "$BASE/b/v1/plans" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"scenario_id":"scn_sa01_w06"}' | python -c 'import sys,json;p=json.load(sys.stdin);print("plan",p["plan_id"]);print(json.dumps(p["metrics"],indent=2))'
