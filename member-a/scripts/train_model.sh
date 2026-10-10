#!/usr/bin/env bash
# Train the shortage model: data/training/{train,test}.csv -> models/shortage_xgb_v1.json + models/model_meta.json.
# Extra arguments are passed through, e.g. `scripts/train_model.sh --validate` or `--train-csv path --models-dir out/`.
set -eu
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || { echo "no .venv - create it first (see README)"; exit 1; }
exec "$PY" -m jp_a.ml.train "$@"
