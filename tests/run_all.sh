#!/usr/bin/env bash
# Regression gate: fast unit tests always; browser E2E when a conversation id is supplied.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== unit =="
.venv/bin/python -m unittest discover -s tests -v

if [[ "${RUN_E2E:-0}" == "1" ]]; then
  echo "== e2e =="
  : "${CONVO:?set CONVO=<conversation-id> to run the browser test}"
  NODE_PATH="${NODE_PATH:-/usr/local/lib/node_modules}" node tests/e2e/deck-studio.js "$CONVO"
else
  echo "(skipping e2e — set RUN_E2E=1 CONVO=<id> to include it)"
fi
