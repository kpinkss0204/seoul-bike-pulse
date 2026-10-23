#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-good}"
if [[ "$MODE" != "good" && "$MODE" != "bad" ]]
then
 echo "Usage: $0 good|bad"
 exit 1
fi

: "${BIKEPULSE_BUCKET:?Missing BIKEPULSE_BUCKET}"
: "${ECS_CLUSTER:?Missing ECS_CLUSTER}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"
: "${RAW_KEY:?Missing RAW_KEY}"
: "${MANIFEST_KEY:?Missing MANIFEST_KEY}"
: "${CURATED_KEY:?Missing CURATED_KEY}"

if [[ "$MODE" == "good" ]]
then
 : "${QUALITY_REPORT_KEY:?Missing QUALITY_REPORT_KEY}"
 INPUT_QUALITY_KEY="$QUALITY_REPORT_KEY"
else
 : "${BAD_QUALITY_REPORT_KEY:?Missing BAD_QUALITY_REPORT_KEY}"
 INPUT_QUALITY_KEY="$BAD_QUALITY_REPORT_KEY"
fi

RUN_ID="i-$(date -u +%Y%m%dT%H%M%S)-${RANDOM}-${MODE}"
REPORT_KEY="curated/_transformation_reports/version=dv001/run_id=${RUN_ID}/transformation_report.json"

mkdir -p data/validation
OVERRIDE_FILE=$(mktemp)
trap 'rm -f "$OVERRIDE_FILE"' EXIT

export INPUT_QUALITY_KEY RUN_ID

python3 - "$OVERRIDE_FILE" <<'PY'
import json
import os
import sys

names = {
 "RAW_KEY": os.environ["RAW_KEY"],
 "MANIFEST_KEY": os.environ["MANIFEST_KEY"],
 "QUALITY_REPORT_KEY": os.environ["INPUT_QUALITY_KEY"],
 "CURATED_KEY": os.environ["CURATED_KEY"],
 "TRANSFORM_RUN_ID": os.environ["RUN_ID"]
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
 for name, value in names.items()
 ]
 }
 ]
}

with open(sys.argv[1], "w") as f:
 json.dump(document, f)
PY

TASK_DEFINITION=""
for ARN in $(aws ecs list-task-definitions \
 --family-prefix bikepulse-dev-processor-task \
 --status ACTIVE \
 --sort DESC \
 --query 'taskDefinitionArns[]' \
 --output text)
do
 CANDIDATE_IMAGE=$(aws ecs describe-task-definition \
 --task-definition "$ARN" \
 --query 'taskDefinition.containerDefinitions[0].image' \
 --output text)
 if [[ "$CANDIDATE_IMAGE" == *"/bikepulse/processor:i001" ]]
 then
 TASK_DEFINITION="$ARN"
 break
 fi
done
if [[ -z "$TASK_DEFINITION" ]]
then
 echo "ERROR: processor:i001 revision not found."
 exit 1
fi

IMAGE=$(aws ecs describe-task-definition \
 --task-definition "$TASK_DEFINITION" \
 --query 'taskDefinition.containerDefinitions[0].image' \
 --output text)

if [[ "$IMAGE" != *"/bikepulse/processor:i001" ]]
then
 echo "ERROR: Expected processor:i001 image."
 echo "Actual image: $IMAGE"
 exit 1
fi

echo "=========================================="
echo "BIKEPULSE PARQUET TRANSFORMATION"
echo "=========================================="
echo "Mode: $MODE"
echo "Run ID: $RUN_ID"
echo "Task Definition: $TASK_DEFINITION"
echo "Input Quality Report: $INPUT_QUALITY_KEY"

RESPONSE=$(aws ecs run-task \
 --cluster "$ECS_CLUSTER" \
 --task-definition "$TASK_DEFINITION" \
 --launch-type FARGATE \
 --platform-version LATEST \
 --count 1 \
 --network-configuration \
 "awsvpcConfiguration={subnets=[$PRIVATE_A],securityGroups=[$SG_MLOPS],assignPublicIp=DISABLED}" \
 --overrides "file://${OVERRIDE_FILE}" \
 --output json)

if ! echo "$RESPONSE" | jq -e '
 (.failures | length == 0)
 and (.tasks | length == 1)
' > /dev/null
then
 echo "$RESPONSE" | jq .
 echo "RUN TASK FAILED"
 exit 1
fi

TASK_ARN=$(echo "$RESPONSE" | jq -r '.tasks[0].taskArn')
echo "Task ARN: $TASK_ARN"

aws ecs wait tasks-stopped \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN"

DETAIL=$(aws ecs describe-tasks \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN" \
 --output json)

DETAIL_FILE="data/validation/i_${MODE}_execution.json"
echo "$DETAIL" > "$DETAIL_FILE"

EXIT_CODE=$(echo "$DETAIL" | jq -r '
 .tasks[0].containers[]
 | select(.name == "processor")
 | .exitCode // -999
')

STOP_REASON=$(echo "$DETAIL" | jq -r '
 .tasks[0].stoppedReason // "none"
')

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
echo "[S3 Transformation Report]"
REPORT_FILE="data/validation/i_${MODE}_s3_report.json"
if ! aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${REPORT_KEY}" \
 "$REPORT_FILE"
then
 echo "ERROR: Transformation report is unavailable."
 echo "Check ECS stoppedReason and CloudWatch Logs."
 exit 1
fi

jq '{
 status,
 run_id,
 output_key,
 source_sha256,
 output,
 error
}' "$REPORT_FILE"

STATUS=$(jq -r '.status' "$REPORT_FILE")

if [[ "$MODE" == "good" ]]
then
 if [[ "$EXIT_CODE" == "0" && "$STATUS" == "PASSED" ]]
 then
 echo "PARQUET TRANSFORMATION TEST: PASSED"
 else
 echo "PARQUET TRANSFORMATION TEST: FAILED"
 exit 1
 fi
else
 if [[ "$EXIT_CODE" == "1" && "$STATUS" == "FAILED" ]]
 then
 echo "INVALID QUALITY REPORT REJECTION: PASSED"
 else
 echo "INVALID QUALITY REPORT REJECTION: FAILED"
 exit 1
 fi
fi

echo "REPORT KEY: $REPORT_KEY"
echo "=========================================="
