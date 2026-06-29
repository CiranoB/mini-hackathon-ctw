#!/bin/sh
# Seeds the ministack S3 store with the vehicle parquet data on `docker compose up`.
# Runs as a one-shot init container after ministack starts, so the buckets and
# parquet files are always present without any manual `mini s3 cp` step.
#
# Talks to ministack DIRECTLY (not through toxiproxy) so the upload is not
# slowed down by the latency toxic.
#
# Override via environment variables:
#   AWS_ENDPOINT_URL       (default http://ministack:4566)
#   VEHICLE_DATA_BUCKET    (default vehicle-data)
#   VEHICLE_DATA_PREFIX    (default parquet/)
#   ATHENA_RESULTS_BUCKET  (default athena-results)
set -eu

ENDPOINT="${AWS_ENDPOINT_URL:-http://ministack:4566}"
BUCKET="${VEHICLE_DATA_BUCKET:-vehicle-data}"
PREFIX="${VEHICLE_DATA_PREFIX:-parquet/}"
RESULTS="${ATHENA_RESULTS_BUCKET:-athena-results}"
SRC="/parquet"

aws() {
  command aws --endpoint-url "$ENDPOINT" "$@"
}

# Wait for the ministack S3 API to be reachable.
i=0
until aws s3 ls >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "ministack S3 not reachable at $ENDPOINT after 60s" >&2
    exit 1
  fi
  sleep 1
done

# Create the data + Athena results buckets (idempotent).
aws s3 mb "s3://$BUCKET" >/dev/null 2>&1 || true
aws s3 mb "s3://$RESULTS" >/dev/null 2>&1 || true

# Upload every parquet file. ministack rejects the default CRC64NVME checksum,
# so force SHA256 via the low-level s3api put-object (the high-level `s3 cp`
# does not accept --checksum-algorithm on this CLI version). Overwrites keep
# the bucket in sync with the files committed under
# infra/resources/bucket_data/parquet.
count=0
for f in "$SRC"/*.parquet; do
  [ -e "$f" ] || { echo "No parquet files found in $SRC" >&2; exit 1; }
  name=$(basename "$f")
  aws s3api put-object \
    --bucket "$BUCKET" \
    --key "${PREFIX}${name}" \
    --body "$f" \
    --checksum-algorithm SHA256 >/dev/null
  count=$((count + 1))
  echo "  uploaded ${name} -> s3://$BUCKET/${PREFIX}${name}"
done

echo "Seed complete: $count parquet file(s) in s3://$BUCKET/$PREFIX"
