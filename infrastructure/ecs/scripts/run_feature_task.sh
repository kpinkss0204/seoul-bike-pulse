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
: "${FEATURE_TASK_DEF_ARN:?Missing FEATURE_TASK_DEF_ARN}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"
: "${CURATED_KEY:?Missing CURATED_KEY}"

if [[ "$MODE" == "good" ]]
then
 : "${TRANSFORM_REPORT_KEY:?Missing TRANSFORM_REPORT_KEY}"
 INPUT_REPORT_KEY="$TRANSFORM_REPORT_KEY"
else
 : "${BAD_FEATURE_INPUT_KEY:?Missing BAD_FEATURE_INPUT_KEY}"
 INPUT_REPORT_KEY="$BAD_FEATURE_INPUT_KEY"
fi

RUN_ID="l-$(date -u +%Y%m%dT%H%M%S)-${RANDOM}-${MODE}"
REPORT_KEY="curated/_feature_reports/version=dv001/run_id=${RUN_ID}/feature_report.json"

mkdir -p data/features/executions

OVERRIDES_FILE=$(mktemp)
trap 'rm -f "$OVERRIDES_FILE"' EXIT

export INPUT_REPORT_KEY RUN_ID
python3 - "$OVERRIDES_FILE" <<'PY'
import json
import os
import sys

values = {
 "CURATED_KEY": os.environ["CURATED_KEY"],
 "TRANSFORM_REPORT_KEY": os.environ["INPUT_REPORT_KEY"],
 "FEATURE_RUN_ID": os.environ["RUN_ID"]
}

document = {
 "containerOverrides": [
 {
 "name": "processor",
 "environment": [
 {
 "name": name,
 "value": value
 }
 for name, value in values.items()
 ]
 }
 ]
}

with open(sys.argv[1], "w") as f:
 json.dump(document, f)
PY

IMAGE=$(aws ecs describe-task-definition \
 --task-definition "$FEATURE_TASK_DEF_ARN" \
 --query 'taskDefinition.containerDefinitions[0].image' \
 --output text)

if [[ "$IMAGE" != *"/bikepulse/processor:l001" ]]
then
 echo "ERROR: Expected processor:l001."
 echo "Actual: $IMAGE"
 exit 1
fi

echo "=========================================="
echo "BIKEPULSE FEATURE ENGINEERING"
echo "=========================================="
echo "Mode: $MODE"
echo "Run ID: $RUN_ID"
echo "Task Definition: $FEATURE_TASK_DEF_ARN"
echo "Input: $CURATED_KEY"
echo "Report: $INPUT_REPORT_KEY"

RESPONSE=$(aws ecs run-task \
 --cluster "$ECS_CLUSTER" \
 --task-definition "$FEATURE_TASK_DEF_ARN" \
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

aws ecs wait tasks-stopped \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN"

DETAIL=$(aws ecs describe-tasks \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN" \
 --output json)

echo "$DETAIL" > "data/features/executions/${RUN_ID}_ecs.json"

EXIT_CODE=$(echo "$DETAIL" \
 | jq -r '
 .tasks[0].containers[]
 | select(.name == "processor")
 | .exitCode // -999
 ')

STOP_REASON=$(echo "$DETAIL" | jq -r '.tasks[0].stoppedReason // "none"')

echo "EXIT CODE: $EXIT_CODE"
echo "STOP REASON: $STOP_REASON"

TASK_ID="${TASK_ARN##*/}"
LOG_STREAM="ecs/processor/${TASK_ID}"

echo ""
echo "[CloudWatch Logs]"
for ATTEMPT in 1 2 3 4 5
do
 if aws logs get-log-events \
 --log-group-name /ecs/bikepulse-dev/processor \
 --log-stream-name "$LOG_STREAM" \
 --query 'events[].message' \
 --output text
 then
  break
 fi
 sleep 3
done

echo ""
echo "[Feature Report]"
REPORT_FILE="data/features/executions/${RUN_ID}_report.json"

if ! aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${REPORT_KEY}" \
 "$REPORT_FILE"
then
 echo "Feature report not found."
 echo "Check ECS stop reason and CloudWatch Logs."
 exit 1
fi

jq '{
 status,
 dataset_version,
 feature_version,
 all_rows,
 eligible_training_rows,
 model_feature_count,
 split_statistics,
 error
}' "$REPORT_FILE"

STATUS=$(jq -r '.status' "$REPORT_FILE")

if [[ "$MODE" == "good" ]]
then
 if [[ "$EXIT_CODE" == "0" && "$STATUS" == "PASSED" ]]
 then
  printf '%s\n' "$RUN_ID" > data/features/executions/last_good_run_id.txt
  echo "FEATURE ENGINEERING TEST: PASSED"
 else
  echo "FEATURE ENGINEERING TEST: FAILED"
  exit 1
 fi
else
 if [[ "$EXIT_CODE" == "1" && "$STATUS" == "FAILED" ]]
 then
  echo "INVALID INPUT REJECTION: PASSED"
 else
  echo "INVALID INPUT REJECTION: FAILED"
  exit 1
 fi
fi

echo "REPORT KEY: $REPORT_KEY"
echo "=========================================="
