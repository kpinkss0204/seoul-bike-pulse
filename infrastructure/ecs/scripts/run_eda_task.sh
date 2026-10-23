#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-good}"

if [[ "$MODE" != "good" && "$MODE" != "bad" ]]
then
  echo "Usage: $0 good|bad"
  exit 1
fi

: "${ECS_CLUSTER:?Missing ECS_CLUSTER}"
: "${BIKEPULSE_BUCKET:?Missing BIKEPULSE_BUCKET}"
: "${CURATED_KEY:?Missing CURATED_KEY}"
: "${EDA_TASK_DEF_ARN:?Missing EDA_TASK_DEF_ARN}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"

if [[ "$MODE" == "good" ]]
then
  : "${TRANSFORM_REPORT_KEY:?Missing TRANSFORM_REPORT_KEY}"
  INPUT_REPORT_KEY="$TRANSFORM_REPORT_KEY"
else
  : "${BAD_TRANSFORM_REPORT_KEY:?Missing BAD_TRANSFORM_REPORT_KEY}"
  INPUT_REPORT_KEY="$BAD_TRANSFORM_REPORT_KEY"
fi

IMAGE=$(aws ecs describe-task-definition \
  --task-definition "$EDA_TASK_DEF_ARN" \
  --query 'taskDefinition.containerDefinitions[0].image' \
  --output text)

if [[ "$IMAGE" != *"/bikepulse/monitor:k001" ]]
then
  echo "ERROR: Expected monitor:k001 image."
  echo "Actual: $IMAGE"
  exit 1
fi

RUN_ID="k-$(date -u +%Y%m%dT%H%M%S)-${RANDOM}-${MODE}"

PREFIX="monitoring/eda-reports/dataset_version=dv001/run_id=${RUN_ID}/"

MANIFEST_KEY="${PREFIX}manifest.json"

mkdir -p data/eda

OVERRIDES_FILE=$(mktemp)

trap 'rm -f "$OVERRIDES_FILE"' EXIT

export INPUT_REPORT_KEY RUN_ID

python3 - "$OVERRIDES_FILE" <<'PY'
import json
import os
import sys

document = {
    "containerOverrides": [
        {
            "name": "monitor",
            "environment": [
                {
                    "name": "CURATED_KEY",
                    "value": os.environ["CURATED_KEY"]
                },
                {
                    "name": "TRANSFORM_REPORT_KEY",
                    "value": os.environ["INPUT_REPORT_KEY"]
                },
                {
                    "name": "EDA_RUN_ID",
                    "value": os.environ["RUN_ID"]
                }
            ]
        }
    ]
}

with open(sys.argv[1], "w") as f:
    json.dump(document, f)
PY

echo "=========================================="
echo "BIKEPULSE ECS EDA ANALYSIS"
echo "=========================================="
echo "Mode: $MODE"
echo "Run ID: $RUN_ID"
echo "Task Definition: $EDA_TASK_DEF_ARN"
echo "Parquet: $CURATED_KEY"
echo "Report: $INPUT_REPORT_KEY"

RESPONSE=$(aws ecs run-task \
  --cluster "$ECS_CLUSTER" \
  --task-definition "$EDA_TASK_DEF_ARN" \
  --launch-type FARGATE \
  --platform-version LATEST \
  --count 1 \
  --network-configuration \
    "awsvpcConfiguration={subnets=[$PRIVATE_A],securityGroups=[$SG_MLOPS],assignPublicIp=DISABLED}" \
  --overrides "file://${OVERRIDES_FILE}" \
  --output json)

if ! echo "$RESPONSE" | jq -e '
  (.failures | length == 0)
  and (.tasks | length == 1)
' > /dev/null
then
  echo "$RESPONSE" | jq .
  echo "EDA RUN TASK: FAILED"
  exit 1
fi

TASK_ARN=$(echo "$RESPONSE" \
  | jq -r '.tasks[0].taskArn')

echo ""
echo "TASK ARN: $TASK_ARN"
echo "Waiting for completion..."

aws ecs wait tasks-stopped \
  --cluster "$ECS_CLUSTER" \
  --tasks "$TASK_ARN"

DETAIL=$(aws ecs describe-tasks \
  --cluster "$ECS_CLUSTER" \
  --tasks "$TASK_ARN" \
  --output json)

EXIT_CODE=$(echo "$DETAIL" \
  | jq -r '
      .tasks[0].containers[]
      | select(.name == "monitor")
      | .exitCode // -999
    ')

STOP_REASON=$(echo "$DETAIL" \
  | jq -r '.tasks[0].stoppedReason // "none"')

echo ""
echo "EXIT CODE: $EXIT_CODE"
echo "STOP REASON: $STOP_REASON"

TASK_ID="${TASK_ARN##*/}"
LOG_STREAM="ecs/monitor/${TASK_ID}"

echo ""
echo "[CloudWatch Logs]"

for ATTEMPT in 1 2 3 4 5
do
  if aws logs get-log-events \
    --log-group-name /ecs/bikepulse-dev/monitor \
    --log-stream-name "$LOG_STREAM" \
    --query 'events[].message' \
    --output text
  then
    break
  fi

  sleep 3
done

if [[ "$MODE" == "good" ]]
then
  if [[ "$EXIT_CODE" != "0" ]]
  then
    echo "EDA TASK: FAILED"
    exit 1
  fi

  echo ""
  echo "[S3 EDA Manifest]"

  aws s3 cp \
    "s3://${BIKEPULSE_BUCKET}/${MANIFEST_KEY}" \
    data/eda/k_latest_manifest.json

  jq '{
    status,
    run_id,
    dataset_version,
    input_parquet_sha256,
    artifact_count: (.artifacts | length)
  }' data/eda/k_latest_manifest.json

  jq -e \
    --arg run_id "$RUN_ID" '
      .status == "PASSED"
      and .run_id == $run_id
      and (.artifacts | length) == 12
    ' data/eda/k_latest_manifest.json \
    > /dev/null

  printf '%s\n' "$RUN_ID" \
    > data/eda/k_last_run_id.txt

  echo "EDA ECS TEST: PASSED"

else
  if [[ "$EXIT_CODE" != "1" ]]
  then
    echo "INVALID INPUT REJECTION: FAILED"
    exit 1
  fi

  EXISTING=$(aws s3api list-objects-v2 \
    --bucket "$BIKEPULSE_BUCKET" \
    --prefix "$MANIFEST_KEY" \
    --query "Contents[?Key=='${MANIFEST_KEY}'].Key" \
    --output text)

  if [[ -n "$EXISTING" ]]
  then
    echo "ERROR: Success manifest exists for rejected input."
    exit 1
  fi

  echo "INVALID INPUT REJECTION: PASSED"
fi

echo "RUN ID: $RUN_ID"
echo "=========================================="
