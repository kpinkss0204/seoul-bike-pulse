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
: "${XGB_TASK_DEF_ARN:?Missing XGB_TASK_DEF_ARN}"
: "${BASELINE_RUN_ID:?Missing BASELINE_RUN_ID}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"

EXPECTED_IMAGE_TAG="${TRAINER_IMAGE_TAG:-n001}"

if [[ "$MODE" == "good" ]]
then
 : "${FEATURE_REPORT_KEY:?Missing FEATURE_REPORT_KEY}"
 INPUT_REPORT_KEY="$FEATURE_REPORT_KEY"
else
 : "${BAD_FEATURE_REPORT_KEY:?Missing BAD_FEATURE_REPORT_KEY}"
 INPUT_REPORT_KEY="$BAD_FEATURE_REPORT_KEY"
fi

IMAGE=$(aws ecs describe-task-definition \
 --task-definition "$XGB_TASK_DEF_ARN" \
 --query 'taskDefinition.containerDefinitions[0].image' \
 --output text)

if [[ "$IMAGE" != *"/bikepulse/trainer:${EXPECTED_IMAGE_TAG}" ]]
then
 echo "ERROR: Unexpected Trainer image."
 echo "Expected tag: $EXPECTED_IMAGE_TAG"
 echo "Actual image: $IMAGE"
 exit 1
fi

RUN_ID="n-$(date -u +%Y%m%dT%H%M%S)-${RANDOM}-${MODE}"
MODEL_PREFIX="models/experiments/feature_version=fv001/run_id=${RUN_ID}/"

mkdir -p data/xgboost/executions

OVERRIDES_FILE=$(mktemp)
trap 'rm -f "$OVERRIDES_FILE"' EXIT

export INPUT_REPORT_KEY RUN_ID
python3 - "$OVERRIDES_FILE" <<'PY'
import json
import os
import sys

values = {
 "FEATURE_REPORT_KEY": os.environ["INPUT_REPORT_KEY"],
 "BASELINE_RUN_ID": os.environ["BASELINE_RUN_ID"],
 "XGB_RUN_ID": os.environ["RUN_ID"]
}

document = {
 "containerOverrides": [
 {
 "name": "trainer",
 "environment": [
 {
 "name": key,
 "value": value
 }
 for key, value in values.items()
 ]
 }
 ]
}

with open(sys.argv[1], "w") as f:
 json.dump(document, f)
PY

echo "=========================================="
echo "BIKEPULSE XGBOOST HYPERPARAMETER TUNING"
echo "=========================================="
echo "Mode: $MODE"
echo "Run ID: $RUN_ID"
echo "Task Definition: $XGB_TASK_DEF_ARN"
echo "Baseline Run: $BASELINE_RUN_ID"
echo "Feature Report: $INPUT_REPORT_KEY"

RESPONSE=$(aws ecs run-task \
 --cluster "$ECS_CLUSTER" \
 --task-definition "$XGB_TASK_DEF_ARN" \
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

TASK_ARN=$(echo "$RESPONSE" | jq -r '.tasks[0].taskArn')
echo "TASK ARN: $TASK_ARN"
echo "Waiting for ECS training task..."

FINAL_STATE=""
for ATTEMPT in $(seq 1 120)
do
 STATE=$(aws ecs describe-tasks \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN" \
 --query 'tasks[0].lastStatus' \
 --output text)
 echo "Attempt=$ATTEMPT Status=$STATE"
 if [[ "$STATE" == "STOPPED" ]]
 then
  FINAL_STATE="STOPPED"
  break
 fi
 sleep 20
done

if [[ "$FINAL_STATE" != "STOPPED" ]]
then
 echo "TRAINING WAIT TIMEOUT"
 echo "Task may still be running:"
 echo "$TASK_ARN"
 echo "Inspect the task before deciding to stop it."
 exit 1
fi

DETAIL=$(aws ecs describe-tasks \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN" \
 --output json)

echo "$DETAIL" > "data/xgboost/executions/${RUN_ID}_ecs.json"

EXIT_CODE=$(echo "$DETAIL" \
 | jq -r '
 .tasks[0].containers[]
 | select(.name == "trainer")
 | .exitCode // -999
 ')

STOP_REASON=$(echo "$DETAIL" | jq -r '.tasks[0].stoppedReason // "none"')

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
echo "[S3 Training Result]"
if [[ "$MODE" == "good" ]]
then
 if [[ "$EXIT_CODE" != "0" ]]
 then
  echo "XGBOOST TRAINING: FAILED"
  exit 1
 fi
 
 REPORT_FILE="data/xgboost/executions/${RUN_ID}_training_report.json"
 MANIFEST_FILE="data/xgboost/executions/${RUN_ID}_manifest.json"
 
 aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${MODEL_PREFIX}training_report.json" \
 "$REPORT_FILE"
 
 aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${MODEL_PREFIX}run_manifest.json" \
 "$MANIFEST_FILE"
 
 jq '{
  status,
  worker_version,
  train_rows,
  validation_rows,
  feature_count,
  cv_folds,
  grid_candidates,
  random_candidates,
  best_xgboost_candidate,
  provisional_overall_leader,
  test_evaluated
 }' "$REPORT_FILE"
 
 jq -e \
 --arg run_id "$RUN_ID" '
  .status == "PASSED"
  and .run_id == $run_id
  and .worker_version == "n001"
  and .feature_count == 19
  and .cv_folds == 3
  and .grid_candidates == 8
  and .random_candidates == 8
  and .test_evaluated == false
 ' "$REPORT_FILE" > /dev/null
 
 jq -e \
 --arg run_id "$RUN_ID" '
  .status == "PASSED"
  and .run_id == $run_id
  and (.artifacts | length) == 12
  and .test_evaluated == false
 ' "$MANIFEST_FILE" > /dev/null
 
 printf '%s\n' "$RUN_ID" > data/xgboost/executions/last_good_run_id.txt
 echo "XGBOOST TRAINING TEST: PASSED"
else
 if [[ "$EXIT_CODE" != "1" ]]
 then
  echo "INVALID INPUT REJECTION: FAILED"
  exit 1
 fi
 
 FAILURE_FILE="data/xgboost/executions/${RUN_ID}_failure_report.json"
 
 aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${MODEL_PREFIX}failure_report.json" \
 "$FAILURE_FILE"
 
 jq -e \
 '.status == "FAILED"' \
 "$FAILURE_FILE" > /dev/null
 
 echo "INVALID INPUT REJECTION: PASSED"
fi

echo "MODEL PREFIX: $MODEL_PREFIX"
echo "=========================================="
