#!/usr/bin/env bash
# Docker is not installed on the build host: simulate the image instead (see scripts/simulate_image.py).
#   scripts/simulate_image.sh                                  # backend/Dockerfile
#   scripts/simulate_image.sh --dockerfile infra/lambda.Dockerfile
# Needs `uv` on PATH. Any python >= 3.11 can drive it (the script only uses the standard library).
set -eu
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
exec "$PY" scripts/simulate_image.py "$@"
