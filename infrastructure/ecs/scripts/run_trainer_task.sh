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
: "${TRAINER_TASK_DEF_ARN:?Missing TRAINER_TASK_DEF_ARN}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"

if [[ "$MODE" == "good" ]]
then
  : "${FEATURE_REPORT_KEY:?Missing FEATURE_REPORT_KEY}"
  INPUT_REPORT_KEY="$FEATURE_REPORT_KEY"
else
  : "${BAD_FEATURE_REPORT_KEY:?Missing BAD_FEATURE_REPORT_KEY}"
  INPUT_REPORT_KEY="$BAD_FEATURE_REPORT_KEY"
fi

RUN_ID="m-$(date -u +%Y%m%dT%H%M%S)-${RANDOM}-${MODE}"

MODEL_PREFIX="models/experiments/feature_version=fv001/run_id=${RUN_ID}/"

mkdir -p data/training/executions

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
            "name": "trainer",
            "environment": [
                {
                    "name": "FEATURE_REPORT_KEY",
                    "value": os.environ["INPUT_REPORT_KEY"]
                },
                {
                    "name": "TRAIN_RUN_ID",
                    "value": os.environ["RUN_ID"]
                }
            ]
        }
    ]
}

with open(sys.argv[1], "w") as f:
    json.dump(document, f)
PY

IMAGE=$(aws ecs describe-task-definition \
  --task-definition "$TRAINER_TASK_DEF_ARN" \
  --query 'taskDefinition.containerDefinitions[0].image' \
  --output text)

if [[ "$IMAGE" != *"/bikepulse/trainer:m001" ]]
then
  echo "ERROR: Expected trainer:m001."
  echo "Actual: $IMAGE"
  exit 1
fi

echo "=========================================="
echo "BIKEPULSE SCIKIT-LEARN TRAINING"
echo "=========================================="
echo "Mode: $MODE"
echo "Run ID: $RUN_ID"
echo "Task Definition: $TRAINER_TASK_DEF_ARN"
echo "Feature Report: $INPUT_REPORT_KEY"

RESPONSE=$(aws ecs run-task \
  --cluster "$ECS_CLUSTER" \
  --task-definition "$TRAINER_TASK_DEF_ARN" \
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
  echo "ECS RUN TASK: FAILED"
  exit 1
fi

TASK_ARN=$(echo "$RESPONSE" \
  | jq -r '.tasks[0].taskArn')

echo "TASK ARN: $TASK_ARN"

aws ecs wait tasks-stopped \
  --cluster "$ECS_CLUSTER" \
  --tasks "$TASK_ARN"

DETAIL=$(aws ecs describe-tasks \
  --cluster "$ECS_CLUSTER" \
  --tasks "$TASK_ARN" \
  --output json)

echo "$DETAIL" \
  > "data/training/executions/${RUN_ID}_ecs.json"

EXIT_CODE=$(echo "$DETAIL" \
  | jq -r '
      .tasks[0].containers[]
      | select(.name == "trainer")
      | .exitCode // -999
    ')

STOP_REASON=$(echo "$DETAIL" \
  | jq -r '.tasks[0].stoppedReason // "none"')

echo ""
echo "EXIT CODE: $EXIT_CODE"
echo "STOP REASON: $STOP_REASON"

TASK_ID="${TASK_ARN##*/}"
LOG_STREAM="ecs/trainer/${TASK_ID}"

echo ""
echo "[CloudWatch Logs]"

for ATTEMPT in 1 2 3 4 5
do
  if aws logs get-log-events \
    --log-group-name /ecs/bikepulse-dev/trainer \
    --log-stream-name "$LOG_STREAM" \
    --query 'events[].message' \
    --output text
  then
    break
  fi

  sleep 3
done

echo ""
echo "[Training Result]"

if [[ "$MODE" == "good" ]]
then
  if [[ "$EXIT_CODE" != "0" ]]
  then
    echo "TRAINING TEST: FAILED"
    exit 1
  fi

  aws s3 cp \
    "s3://${BIKEPULSE_BUCKET}/${MODEL_PREFIX}training_report.json" \
    "data/training/executions/${RUN_ID}_training_report.json"

  aws s3 cp \
    "s3://${BIKEPULSE_BUCKET}/${MODEL_PREFIX}run_manifest.json" \
    "data/training/executions/${RUN_ID}_manifest.json"

  REPORT_FILE="data/training/executions/${RUN_ID}_training_report.json"
  MANIFEST_FILE="data/training/executions/${RUN_ID}_manifest.json"

  jq '{
    status,
    run_id,
    train_rows,
    validation_rows,
    feature_count,
    selected_baseline,
    test_evaluated
  }' "$REPORT_FILE"

  jq -e \
    --arg run_id "$RUN_ID" '
      .status == "PASSED"
      and .run_id == $run_id
      and .feature_count == 19
      and .test_evaluated == false
    ' "$REPORT_FILE" > /dev/null

  jq -e \
    --arg run_id "$RUN_ID" '
      .status == "PASSED"
      and .run_id == $run_id
      and (.artifacts | length) == 9
    ' "$MANIFEST_FILE" > /dev/null

  printf '%s\n' "$RUN_ID" \
    > data/training/executions/last_good_run_id.txt

  echo "MODEL TRAINING TEST: PASSED"

else
  if [[ "$EXIT_CODE" != "1" ]]
  then
    echo "INVALID INPUT REJECTION: FAILED"
    exit 1
  fi

  aws s3 cp \
    "s3://${BIKEPULSE_BUCKET}/${MODEL_PREFIX}failure_report.json" \
    "data/training/executions/${RUN_ID}_failure_report.json"

  jq -e \
    '.status == "FAILED"' \
    "data/training/executions/${RUN_ID}_failure_report.json" \
    > /dev/null

  echo "INVALID INPUT REJECTION: PASSED"
fi

echo "MODEL PREFIX: $MODEL_PREFIX"
echo "=========================================="
