#!/bin/bash
# Run the corpus in fixed batches of 10 cases, one report per batch.
#
#   tests/run_batches.sh [outdir]     # default: tests/results/full-<stamp>
#
# Explicit id slices, not `--resume`: a failed case must not be retried in the
# next batch (that would silently re-shape the batches and hide the first failure).
set -u
cd "$(dirname "$0")/.."

OUT=${1:-tests/results/full-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$OUT"

# The runner resolves deck ids on the host, so it needs the server's output dir.
export PPT_OUTPUT_DIR=${PPT_OUTPUT_DIR:-/home/hyma/dev/librechat/uploads/decks}
export PPT_TEMPLATE_PATH=${PPT_TEMPLATE_PATH:-$PWD/templates}
export PPT_UPLOAD_DIR=${PPT_UPLOAD_DIR:-/home/hyma/dev/librechat/uploads}

mapfile -t BATCHES < <(.venv/bin/python - <<'PY'
import json
ids = [c["id"] for c in json.load(open("tests/cases.json"))["cases"]]
for i in range(0, len(ids), 10):
    print(",".join(ids[i:i + 10]))
PY
)
total=${#BATCHES[@]}

for ((i = 0; i < total; i++)); do
  echo "=== batch $((i + 1))/$total start $(date '+%F %T') ===" >>"$OUT/batches.log"
  .venv/bin/python tests/run_suite.py --ids "${BATCHES[$i]}" --outdir "$OUT" >>"$OUT/batches.log" 2>&1
  echo "=== batch $((i + 1))/$total end $(date '+%F %T') ===" >>"$OUT/batches.log"
done
echo "=== all $total batches done $(date '+%F %T') ===" >>"$OUT/batches.log"
