#!/usr/bin/env bash
# Single-container entrypoint for hosts that do not run separate worker
# services (Render web service, one Fly machine). The worker and the API share
# the same database and DATA_DIR.
set -euo pipefail

PORT="${PORT:-8000}"

python -m backend.engine.worker &
worker_pid=$!

uvicorn backend.api.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --proxy-headers \
  --forwarded-allow-ips='*' &
api_pid=$!

shutdown() {
  kill -TERM "$api_pid" "$worker_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
  wait "$worker_pid" 2>/dev/null || true
}
trap shutdown INT TERM

wait -n "$api_pid" "$worker_pid"
status=$?
shutdown
exit "$status"
