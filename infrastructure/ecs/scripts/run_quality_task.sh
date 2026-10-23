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

if [[ "$MODE" == "good" ]]
then
 INPUT_KEY="raw/source=uci-seoul-bike/version=dv001/SeoulBikeData.csv"
 INPUT_MANIFEST="raw/source=uci-seoul-bike/version=dv001/manifest.json"
else
 INPUT_KEY="raw/_quality_tests/invalid-hour/SeoulBikeData.csv"
 INPUT_MANIFEST="raw/_quality_tests/invalid-hour/manifest.json"
fi

RUN_ID="h-$(date -u +%Y%m%dT%H%M%SZ)-${MODE}"
REPORT_KEY="curated/_quality_reports/version=dv001/run_id=${RUN_ID}/quality_report.json"

mkdir -p data/validation
OVERRIDES_FILE=$(mktemp)
trap 'rm -f "$OVERRIDES_FILE"' EXIT

export INPUT_KEY INPUT_MANIFEST RUN_ID

python3 - "$OVERRIDES_FILE" <<'PY'
import json
import os
import sys

document = {
 "containerOverrides": [
 {
 "name": "processor",
 "environment": [
 {
 "name": "RAW_KEY",
 "value": os.environ["INPUT_KEY"]
 },
 {
 "name": "MANIFEST_KEY",
 "value": os.environ["INPUT_MANIFEST"]
 },
 {
 "name": "VALIDATION_RUN_ID",
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
echo "BIKEPULSE ECS QUALITY VALIDATION"
echo "=========================================="
echo "Mode : $MODE"
echo "Run ID : $RUN_ID"
echo "Input : $INPUT_KEY"
echo ""

# Check that the latest definition uses the H-stage image.
QUALITY_TASK_DEF_ARN=""
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
 if [[ "$CANDIDATE_IMAGE" == *"/bikepulse/processor:h001" ]]
 then
 QUALITY_TASK_DEF_ARN="$ARN"
 break
 fi
done
if [[ -z "$QUALITY_TASK_DEF_ARN" ]]
then
 echo "ERROR: H-stage processor:h001 task revision not found."
 exit 1
fi
echo "QUALITY TASK DEFINITION: $QUALITY_TASK_DEF_ARN"
CURRENT_IMAGE=$(aws ecs describe-task-definition \
 --task-definition "$QUALITY_TASK_DEF_ARN" \
 --query 'taskDefinition.containerDefinitions[0].image' \
 --output text)

if [[ "$CURRENT_IMAGE" != *"/bikepulse/processor:h001" ]]
then
 echo "ERROR: Latest task definition does not use h001."
 echo "Image: $CURRENT_IMAGE"
 exit 1
fi

RESPONSE=$(aws ecs run-task \
 --cluster "$ECS_CLUSTER" \
 --task-definition "$QUALITY_TASK_DEF_ARN" \
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

echo "$DETAIL" \
 > "data/validation/h_${MODE}_execution.json"

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
echo ""
echo "[CloudWatch Logs]"

TASK_ID="${TASK_ARN##*/}"
LOG_STREAM="ecs/processor/${TASK_ID}"

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
echo "[Quality Report]"
REPORT=$(aws s3 cp \
 "s3://${BIKEPULSE_BUCKET}/${REPORT_KEY}" \
 -)

echo "$REPORT" | jq '{
 run_id,
 status,
 error_count,
 metrics,
 errors,
 sample_issues
}'

REPORT_STATUS=$(echo "$REPORT" | jq -r '.status')

if [[ "$MODE" == "good" ]]
then
 if [[ "$EXIT_CODE" == "0" && "$REPORT_STATUS" == "PASSED" ]]
 then
 echo "GOOD DATA TEST: PASSED"
 else
 echo "GOOD DATA TEST: FAILED"
 exit 1
 fi
else
 if [[ "$EXIT_CODE" == "1" && "$REPORT_STATUS" == "FAILED" ]]
 then
 echo "BAD DATA REJECTION TEST: PASSED"
 else
 echo "BAD DATA REJECTION TEST: FAILED"
 exit 1
 fi
fi

echo "REPORT S3 KEY: $REPORT_KEY"
echo "=========================================="
