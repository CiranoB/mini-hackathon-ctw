#!/bin/sh
# Applies the simulated network latency toxic to the ministack proxy.
# Runs as a one-shot init container after toxiproxy starts, so the delay
# is always present without a manual ./latency.sh call.
#
# Override via environment variables:
#   TOXIPROXY_ADMIN  (default http://toxiproxy:8474)
#   PROXY            (default ministack)
#   LATENCY_MS       (default 150)
#   JITTER_MS        (default 0)
set -eu

ADMIN="${TOXIPROXY_ADMIN:-http://toxiproxy:8474}"
PROXY="${PROXY:-ministack}"
LATENCY="${LATENCY_MS:-150}"
JITTER="${JITTER_MS:-0}"

# Wait for the toxiproxy admin API to be reachable.
i=0
until curl -fsS "$ADMIN/proxies" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "toxiproxy admin not reachable at $ADMIN after 60s" >&2
    exit 1
  fi
  sleep 1
done

# (Re)create the latency toxic idempotently.
curl -fsS -X DELETE "$ADMIN/proxies/$PROXY/toxics/latency_downstream" >/dev/null 2>&1 || true
curl -fsS -X POST "$ADMIN/proxies/$PROXY/toxics" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"latency_downstream\",\"type\":\"latency\",\"stream\":\"downstream\",\"attributes\":{\"latency\":$LATENCY,\"jitter\":$JITTER}}" >/dev/null

echo "Latency toxic applied: ${LATENCY}ms +/- ${JITTER}ms on '$PROXY'"
