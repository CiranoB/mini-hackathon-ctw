#!/usr/bin/env bash
# Performance test runner for the /vehicle-summary endpoint, powered by Locust.
#
# Locust is the Python equivalent of Gatling — it gives you a live web UI with
# real-time response-time / RPS charts and can export a standalone HTML report.
#
# Usage:
#   ./run.sh            # headless: warmup + measured run, writes HTML + CSV report
#   ./run.sh web        # live web UI with Gatling-style charts (http://localhost:8089)
#
# Tunables (env vars, with defaults):
#   HOST=http://localhost:8000   target API base URL
#   USERS=1                      concurrent simulated users
#   SPAWN_RATE=1                 users started per second
#   WARMUP_SECONDS=5             warm-up window (discarded from the report)
#   RUN_SECONDS=60               measured window
#   TARGET_REQUESTS=100          requests to spread across the measured window

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="${HOST:-http://localhost:8000}"
USERS="${USERS:-1}"
SPAWN_RATE="${SPAWN_RATE:-1}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-5}"
export RUN_SECONDS="${RUN_SECONDS:-60}"
export TARGET_REQUESTS="${TARGET_REQUESTS:-100}"

# Total run time = warmup + measured window (warmup stats are reset away).
TOTAL_RUNTIME=$(python3 -c "print(int(float('${WARMUP_SECONDS}') + float('${RUN_SECONDS}')))")

MODE="${1:-headless}"

REPORT_DIR="$SCRIPT_DIR/reports"
mkdir -p "$REPORT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ "$MODE" == "web" ]]; then
  echo "Starting Locust web UI at http://localhost:8089 (target: $HOST)"
  echo "Set users=$USERS, run-time=${TOTAL_RUNTIME}s in the UI, then Start."
  exec uv run --with locust locust \
    -f locustfile.py \
    --host "$HOST"
fi

echo "Running headless load test against $HOST"
echo "  warmup=${WARMUP_SECONDS}s  measured=${RUN_SECONDS}s  target=${TARGET_REQUESTS} reqs  users=${USERS}"
echo "  total run-time=${TOTAL_RUNTIME}s"
echo

uv run --with locust locust \
  -f locustfile.py \
  --headless \
  --host "$HOST" \
  --users "$USERS" \
  --spawn-rate "$SPAWN_RATE" \
  --run-time "${TOTAL_RUNTIME}s" \
  --html "$REPORT_DIR/report_${STAMP}.html" \
  --csv "$REPORT_DIR/stats_${STAMP}" \
  --csv-full-history

echo
echo "HTML report: $REPORT_DIR/report_${STAMP}.html"
echo "CSV stats:   $REPORT_DIR/stats_${STAMP}_stats.csv"
