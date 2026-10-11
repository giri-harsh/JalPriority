#!/usr/bin/env bash
# Train the shortage model: data/training/{train,test}.csv -> models/shortage_xgb_v1.json + models/model_meta.json.
# Always records the train-only holdout check (sa_30..sa_33) in model_meta.json "validation", so a plain run
# reproduces the committed artefacts (only "trained_at" changes). The test split is scored exactly once.
# Extra arguments are passed through, e.g. `scripts/train_model.sh --train-csv a.csv --test-csv b.csv --models-dir out/`.
set -eu
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || { echo "no .venv - create it first (see README)"; exit 1; }
exec "$PY" -m jp_a.ml.train --validate "$@"
