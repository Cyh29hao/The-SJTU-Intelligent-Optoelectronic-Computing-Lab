#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".venv" ]; then
  echo ".venv is missing. Run: bash scripts/jcloud_bootstrap.sh"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

LOG_FILE="/tmp/lab-web.log"
PORT_VALUE="${PORT:-5000}"

echo "==> Starting Flask app on 127.0.0.1:$PORT_VALUE"
PORT="$PORT_VALUE" python app.py >"$LOG_FILE" 2>&1 &
APP_PID=$!
trap 'kill "$APP_PID" 2>/dev/null || true' EXIT

sleep 4

echo
echo "==> Last 20 log lines"
tail -n 20 "$LOG_FILE" || true

echo
echo "==> Local HTTP probe"
curl -I "http://127.0.0.1:$PORT_VALUE"

echo
echo "Smoke test passed."
