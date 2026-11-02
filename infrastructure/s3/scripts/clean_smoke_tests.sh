#!/usr/bin/env bash

set -euo pipefail

: "${BIKEPULSE_BUCKET:?BIKEPULSE_BUCKET is required}"

TEST_KEYS=(
  "curated/_smoke_tests/processor.json"
  "models/_smoke_tests/trainer.json"
  "monitoring/_smoke_tests/monitor.json"
)

for KEY in "${TEST_KEYS[@]}"
do
  echo "Cleaning: $KEY"

  OBJECTS=$(aws s3api list-object-versions \
    --bucket "$BIKEPULSE_BUCKET" \
    --prefix "$KEY" \
    --output json \
    | jq -c --arg key "$KEY" '
      [
        (.Versions[]?
          | select(.Key == $key)
          | {Key, VersionId}),
        (.DeleteMarkers[]?
          | select(.Key == $key)
          | {Key, VersionId})
      ]
    ')

  if [[ "$OBJECTS" != "[]" ]]
  then
    aws s3api delete-objects \
      --bucket "$BIKEPULSE_BUCKET" \
      --delete "{\"Objects\":$OBJECTS,\"Quiet\":true}" \
      > /dev/null
  fi

  echo "CLEANUP COMPLETE: $KEY"
done
