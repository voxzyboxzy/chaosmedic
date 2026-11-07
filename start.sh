#!/usr/bin/env bash
# One command for the replay demo. `./start.sh lab` starts the Compose lab.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
MODE="${1:-demo}"

if [[ "$MODE" == "lab" ]]; then
  exec docker compose up --build
fi

python3 -m pip install -q -r backend/requirements.txt
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi
mkdir -p data
export PYTHONPATH="$ROOT/backend:$ROOT"
export CHAOSMEDIC_MODE=replay
export CHAOSMEDIC_DIAGNOSER=rules
export CHAOSMEDIC_DB="$ROOT/data/chaosmedic.db"

python3 -m uvicorn chaosmedic.main:app --host 0.0.0.0 --port 8080 &
API_PID=$!
cleanup() {
  kill "$API_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
ready=0
for _ in $(seq 1 50); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.1
done
if [[ "$ready" != 1 ]]; then
  echo "ChaosMedic API did not start" >&2
  exit 1
fi
cd frontend
npm run dev -- --host 0.0.0.0 --port 5173
