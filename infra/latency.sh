#!/usr/bin/env bash
# Toggle simulated network latency on the ministack proxy.
#
# Usage:
#   ./latency.sh on  [latency_ms] [jitter_ms]   # add/refresh latency (default 500 100)
#   ./latency.sh off                            # remove all latency
#   ./latency.sh status                         # show current toxics
#
# Requires the toxiproxy service to be running (docker compose up).

set -euo pipefail

ADMIN="${TOXIPROXY_ADMIN:-http://localhost:8474}"
PROXY="ministack"

cmd="${1:-status}"

case "$cmd" in
  on)
    latency="${2:-500}"
    jitter="${3:-100}"
    # delete existing toxic if present (ignore failure), then (re)create it
    curl -fsS -X DELETE "$ADMIN/proxies/$PROXY/toxics/latency_downstream" >/dev/null 2>&1 || true
    curl -fsS -X POST "$ADMIN/proxies/$PROXY/toxics" \
      -H 'Content-Type: application/json' \
      -d "{\"name\":\"latency_downstream\",\"type\":\"latency\",\"stream\":\"downstream\",\"attributes\":{\"latency\":$latency,\"jitter\":$jitter}}" >/dev/null
    echo "Latency enabled: ${latency}ms +/- ${jitter}ms on '$PROXY'"
    ;;
  off)
    curl -fsS -X DELETE "$ADMIN/proxies/$PROXY/toxics/latency_downstream" >/dev/null 2>&1 || true
    echo "Latency removed on '$PROXY'"
    ;;
  status)
    curl -fsS "$ADMIN/proxies/$PROXY/toxics"
    echo
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    echo "Usage: $0 {on [latency_ms] [jitter_ms]|off|status}" >&2
    exit 1
    ;;
esac
