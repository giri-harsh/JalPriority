#!/usr/bin/env bash
# Run A's API on :8001 (and optionally the harness on :5174 with `--with-harness`).
set -eu
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || { echo "no .venv - run: uv venv -p 3.12 .venv && uv pip install -p $PY -r backend/requirements.txt -r backend/requirements-dev.txt && uv pip install -p $PY -e backend --no-deps"; exit 1; }
PORT="${A_PORT:-8001}"
if [ "${1:-}" = "--with-harness" ]; then
  (cd dashboard && npm run dev) &
  trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
fi
exec "$PY" -m uvicorn jp_a.main:app --app-dir backend --host 0.0.0.0 --port "$PORT"
