#!/usr/bin/env bash
# Runs the event-evening load test (loadtest/locustfile.py) against a local
# instance of the app, never a deployed one:
#   1. starts loadtest/stub_upstreams.py, a stand-in for OSRM and
#      transport.opendata.ch (so no one else's server gets the load);
#   2. fills a fresh database with the demo drives (routes from the stub);
#   3. starts the app (one uvicorn worker, like the server) on APP_CPUS;
#   4. runs Locust, pinned, headless, and samples the app's memory and CPU.
#
#   loadtest/run.sh [users] [duration] [spawn rate/s]     e.g. 300 5m 10
#
# Environment: APP_CPUS (default "0,1": 2 CPUs, like a t4g server),
# LOCATIONS_DELAY / CONNECTIONS_DELAY (stub transit response times, s),
# WORK_DIR (default loadtest/.work, gitignored). Results: $WORK_DIR/*.csv
# and a summary on stdout. Needs uv and taskset.
set -euo pipefail

USERS=${1:-200}
DURATION=${2:-5m}
RATE=${3:-10}
LOCUST_VERSION=2.47.0
APP_PORT=8000
STUB_PORT=8081

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK_DIR:-$ROOT/loadtest/.work}
mkdir -p "$WORK"
cd "$ROOT"
uv sync --quiet

export DB_PATH="$WORK/loadtest.db"
export OSRM_URL="http://127.0.0.1:$STUB_PORT"
export TRANSPORT_API_URL="http://127.0.0.1:$STUB_PORT/v1"
PY="$ROOT/.venv/bin/python"

PIDS=()
trap 'kill "${PIDS[@]}" 2> /dev/null || true' EXIT

"$PY" loadtest/stub_upstreams.py --port "$STUB_PORT" \
  --locations-delay "${LOCATIONS_DELAY:-0.15}" --connections-delay "${CONNECTIONS_DELAY:-0.5}" &
PIDS+=($!)
sleep 1

if [ ! -f "$DB_PATH" ]; then
  echo "Filling the database with demo drives..."
  PYTHONPATH=backend "$PY" backend/maintenance.py > /dev/null
fi

taskset -c "${APP_CPUS:-0,1}" "$PY" -m uvicorn main:app --app-dir backend --port "$APP_PORT" \
  --log-level warning --no-access-log &
APP_PID=$!
PIDS+=($APP_PID)
for _ in $(seq 1 120); do
  curl -fs -o /dev/null "http://127.0.0.1:$APP_PORT/api/health" && break
  sleep 0.5
done
sleep 5 # the startup maintenance run (nothing to do: the database is full)

cpu_ticks() { awk '{print $14 + $15}' "/proc/$APP_PID/stat"; }
TICKS0=$(cpu_ticks)
T0=$(date +%s)
: > "$WORK/rss_kb.txt"
(
  while kill -0 "$APP_PID" 2> /dev/null; do
    awk '/VmRSS/ {print $2}' "/proc/$APP_PID/status" >> "$WORK/rss_kb.txt"
    sleep 2
  done
) &
PIDS+=($!)

uvx --from "locust==$LOCUST_VERSION" locust -f loadtest/locustfile.py --headless \
  --host "http://127.0.0.1:$APP_PORT" -u "$USERS" -r "$RATE" -t "$DURATION" \
  --csv "$WORK/result" --only-summary --loglevel WARNING || true

T1=$(date +%s)
TICKS1=$(cpu_ticks)
HZ=$(getconf CLK_TCK)
echo
echo "App process: RSS at start $(head -1 "$WORK/rss_kb.txt" | awk '{printf "%d", $1 / 1024}') MB," \
  "max $(sort -n "$WORK/rss_kb.txt" | tail -1 | awk '{printf "%d", $1 / 1024}') MB," \
  "peak (VmHWM) $(awk '/VmHWM/ {printf "%d", $2 / 1024}' "/proc/$APP_PID/status") MB;" \
  "CPU $(awk -v t=$((TICKS1 - TICKS0)) -v hz="$HZ" -v s=$((T1 - T0)) 'BEGIN {printf "%.2f", t / hz / s}') cores on average"
