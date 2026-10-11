#!/usr/bin/env bash
# Run the whole stack locally without Docker (INTEGRATION.md 2.6). Ctrl+C stops everything.
#   A :8001 (uvicorn jp_a.main:app)   B :8002 (B_USE_MOCK_A=false)   B's Vite shell :5173 (real A screens)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
integration="$(dirname "$here")"
repo="$(dirname "$integration")"

[ -f "$integration/.env" ] || { echo "integration/.env missing: copy .env.example and set SHARED_JWT_SECRET" >&2; exit 1; }
set -a; . "$integration/.env"; set +a
export SHARED_ENV=local A_AUTH_DISABLED=false
export A_CORS_ORIGINS=http://localhost:5173 B_CORS_ORIGINS=http://localhost:5173
export B_USE_MOCK_A=false B_UPSTREAM_A_URL=http://localhost:8001
export B_SQLITE_PATH="$repo/member-b/var/jp_b.sqlite3"
export VITE_B_USE_MOCK_A_SCREENS=false VITE_B_A_SCREENS_PATH=../../member-a/dashboard/src/screens/index.ts
mkdir -p "$repo/member-b/var"

pyA="$repo/member-a/.venv/bin/python"; [ -x "$pyA" ] || pyA="$repo/member-a/.venv/Scripts/python.exe"
pyB="$repo/member-b/.venv/bin/python"; [ -x "$pyB" ] || pyB="$repo/member-b/.venv/Scripts/python.exe"

trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
(cd "$repo/member-a" && exec "$pyA" -m uvicorn jp_a.main:app --app-dir backend --port 8001) &
(cd "$repo/member-b" && exec "$pyB" -m uvicorn jp_b.main:app --app-dir backend --port 8002) &
(cd "$repo/member-b/dashboard" && exec npm run dev) &
echo "A http://localhost:8001 | B http://localhost:8002 | shell http://localhost:5173"
wait
