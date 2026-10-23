#!/usr/bin/env bash
set -euo pipefail

: "${BIKEPULSE_BUCKET:?BIKEPULSE_BUCKET is required}"

PREFIXES=(
  "raw/"
  "raw/source=uci-seoul-bike/"
  "raw/source=uci-seoul-bike/version=dv001/"
  "curated/"
  "curated/dataset=seoul-bike/"
  "curated/dataset=seoul-bike/version=dv001/"
  "features/"
  "features/version=fv001/"
  "splits/"
  "splits/version=dv001/"
  "models/"
  "models/model_id=bike-demand/"
  "models/model_id=bike-demand/version=v001/"
  "models/model_id=bike-demand/version=v002/"
  "monitoring/"
  "monitoring/drift-reports/"
  "monitoring/evaluation-reports/"
  "prediction-logs/"
  "athena-results/"
  "system/"
  "system/healthchecks/"
)

echo "=============================================="
echo "BIKEPULSE S3 DATA LAKE INITIALIZATION"
echo "=============================================="
echo "Bucket: $BIKEPULSE_BUCKET"

for PREFIX in "${PREFIXES[@]}"
do
  if aws s3api head-object \
    --bucket "$BIKEPULSE_BUCKET" \
    --key "$PREFIX" \
    > /dev/null 2>&1
  then
    echo "EXISTS : $PREFIX"
  else
    aws s3api put-object \
      --bucket "$BIKEPULSE_BUCKET" \
      --key "$PREFIX" \
      > /dev/null
    echo "CREATED: $PREFIX"
  fi
done

echo "=============================================="
echo "RESULT: DATA LAKE PREFIXES READY"
echo "=============================================="
