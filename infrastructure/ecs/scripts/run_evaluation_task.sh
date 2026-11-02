#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?Usage: run_evaluation_task.sh lock|evaluate}"
if [[ "$MODE" != "lock" && "$MODE" != "evaluate" ]]
then
 echo "Usage: $0 lock|evaluate"
 exit 1
fi

: "${ECS_CLUSTER:?Missing ECS_CLUSTER}"
: "${BIKEPULSE_BUCKET:?Missing BIKEPULSE_BUCKET}"
: "${EVALUATOR_TASK_DEF_ARN:?Missing EVALUATOR_TASK_DEF_ARN}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"
: "${SELECTION_ID:?Missing SELECTION_ID}"

if [[ "$MODE" == "lock" ]]
then
 : "${BASELINE_RUN_ID:?Missing BASELINE_RUN_ID}"
 : "${XGB_RUN_ID:?Missing XGB_RUN_ID}"
 : "${FEATURE_REPORT_KEY:?Missing FEATURE_REPORT_KEY}"
else
 export EVALUATION_RUN_ID="o-$(date -u +%Y%m%dT%H%M%S)-${RANDOM}"
fi

IMAGE=$(aws ecs describe-task-definition \
 --task-definition "$EVALUATOR_TASK_DEF_ARN" \
 --query 'taskDefinition.containerDefinitions[0].image' \
 --output text)

EXPECTED_TAG="${EVALUATOR_IMAGE_TAG:-o001}"
if [[ "$IMAGE" != *"/bikepulse/trainer:${EXPECTED_TAG}" ]]
then
 echo "ERROR: Unexpected evaluator image."
 echo "Actual: $IMAGE"
 exit 1
fi

OVERRIDES_FILE=$(mktemp)
trap 'rm -f "$OVERRIDES_FILE"' EXIT

export MODE

python3 - "$OVERRIDES_FILE" <<'PY'
import json
import os
import sys

names = [
 "SELECTION_ID"
]
if os.environ["MODE"] == "lock":
 names += [
 "BASELINE_RUN_ID",
 "XGB_RUN_ID",
 "FEATURE_REPORT_KEY"
 ]
else:
 names += [
 "EVALUATION_RUN_ID"
 ]

override = {
 "containerOverrides": [
 {
 "name": "trainer",
 "command": [
 "--mode",
 os.environ["MODE"]
 ],
 "environment": [
 {
 "name": name,
 "value": os.environ[name]
 }
 for name in names
 ]
 }
 ]
}

with open(sys.argv[1], "w") as f:
 json.dump(override, f)
PY

echo "=========================================="
echo "BIKEPULSE FINAL MODEL EVALUATION"
echo "=========================================="
echo "Mode: $MODE"
echo "Selection ID: $SELECTION_ID"
echo "Task Definition: $EVALUATOR_TASK_DEF_ARN"

if [[ "$MODE" == "evaluate" ]]
then
 echo "Evaluation Run ID: $EVALUATION_RUN_ID"
fi

RESPONSE=$(aws ecs run-task \
 --cluster "$ECS_CLUSTER" \
 --task-definition "$EVALUATOR_TASK_DEF_ARN" \
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
 exit 1
fi

TASK_ARN=$(echo "$RESPONSE" \
 | jq -r '.tasks[0].taskArn')
echo "TASK ARN: $TASK_ARN"

FINISHED="false"
for ATTEMPT in {1..90}
do
 STATE=$(aws ecs describe-tasks \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN" \
 --query 'tasks[0].lastStatus' \
 --output text)
 echo "Attempt=$ATTEMPT ECS State=$STATE"
 if [[ "$STATE" == "STOPPED" ]]
 then
 FINISHED="true"
 break
 fi
 sleep 10
done

if [[ "$FINISHED" != "true" ]]
then
 echo "ERROR: ECS task wait timeout."
 echo "Inspect task: $TASK_ARN"
 exit 1
fi

DETAIL=$(aws ecs describe-tasks \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN" \
 --output json)

EXIT_CODE=$(echo "$DETAIL" \
 | jq -r '
 .tasks[0].containers[]
 | select(.name == "trainer")
 | .exitCode // -999
 ')
STOP_REASON=$(echo "$DETAIL" \
 | jq -r '.tasks[0].stoppedReason // "unknown"')

echo "EXIT CODE: $EXIT_CODE"
echo "STOP REASON: $STOP_REASON"

TASK_ID="${TASK_ARN##*/}"

echo ""
echo "[CloudWatch Logs]"
for ATTEMPT in 1 2 3 4 5
do
 if aws logs get-log-events \
 --log-group-name /ecs/bikepulse-dev/trainer \
 --log-stream-name "ecs/trainer/${TASK_ID}" \
 --query 'events[].message' \
 --output text
 then
 break
 fi
 sleep 3
done

if [[ "$EXIT_CODE" != "0" ]]
then
 echo "MODEL EVALUATION TASK: FAILED"
 exit 1
fi

if [[ "$MODE" == "lock" ]]
then
 PREFIX="models/selections/feature_version=fv001/selection_id=${SELECTION_ID}/"
 aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${PREFIX}selection.json" \
 data/evaluation/results/selection.json
 aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${PREFIX}selection_manifest.json" \
 data/evaluation/results/selection_manifest.json

 jq -e \
 --arg id "$SELECTION_ID" '
 .status == "LOCKED"
 and .selection_id == $id
 and .test_evaluated == false
 ' data/evaluation/results/selection.json \
 > /dev/null

 echo "[Locked Model]"
 jq '{
 status,
 stage,
 model_name,
 validation_mae,
 source_run_id,
 source_model_key,
 source_model_sha256,
 test_evaluated
 }' data/evaluation/results/selection.json

 echo "MODEL SELECTION LOCK: PASSED"
else
 PREFIX="models/evaluations/feature_version=fv001/run_id=${EVALUATION_RUN_ID}/"
 aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${PREFIX}evaluation_report.json" \
 data/evaluation/results/evaluation_report.json
 aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${PREFIX}evaluation_manifest.json" \
 data/evaluation/results/evaluation_manifest.json

 jq -e \
 --arg id "$EVALUATION_RUN_ID" '
 .status == "PASSED"
 and .worker_version == "o001"
 and .evaluation_run_id == $id
 and .test_evaluated == true
 and .model_retrained == false
 ' data/evaluation/results/evaluation_report.json \
 > /dev/null

 jq -e '
 .status == "PASSED"
 and (.artifacts | length) == 11
 ' data/evaluation/results/evaluation_manifest.json \
 > /dev/null

 printf '%s\n' "$EVALUATION_RUN_ID" \
 > data/evaluation/executions/last_good_run_id.txt

 echo "[Final Test Metrics]"
 jq '{
 model_name,
 model_stage,
 train_mae,
 selection_validation_mae,
 test_rows,
 test_metrics
 }' data/evaluation/results/evaluation_report.json

 echo "FINAL TEST EVALUATION: PASSED"
fi

echo "=========================================="
