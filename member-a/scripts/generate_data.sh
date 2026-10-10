#!/usr/bin/env bash
# Regenerate member-a/data: the 3 seed scenarios, hidden truth, labels and the training CSVs.
# Deterministic: the same seed always produces byte-identical files.
#   scripts/generate_data.sh              # seed from A_SEED (default 42)
#   scripts/generate_data.sh --seed 7     # explicit seed
# Works under Git Bash on Windows and on Linux/macOS.
set -eu
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || { echo "generate_data: no .venv found in member-a (see README setup)" >&2; exit 1; }
exec "$PY" -m jp_a.data.cli --out data "$@"
