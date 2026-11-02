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
: "${PACKAGER_TASK_DEF_ARN:?Missing PACKAGER_TASK_DEF_ARN}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"
: "${SELECTION_ID:?Missing SELECTION_ID}"
: "${EVALUATION_RUN_ID:?Missing EVALUATION_RUN_ID}"
: "${MODEL_VERSION:?Missing MODEL_VERSION}"

RUN_SELECTION_ID="$SELECTION_ID"

if [[ "$MODE" == "bad" ]]
then
  RUN_SELECTION_ID="sel-nonexistent-$(date -u +%Y%m%dT%H%M%S)"
fi

PACKAGE_PREFIX="models/registry-candidates/model_id=bike-demand/version=${MODEL_VERSION}/"

mkdir -p data/package/executions

OVERRIDES_FILE=$(mktemp)

trap 'rm -f "$OVERRIDES_FILE"' EXIT

export RUN_SELECTION_ID

python3 - "$OVERRIDES_FILE" <<'PY'
import json
import os
import sys

values = {
    "SELECTION_ID": os.environ["RUN_SELECTION_ID"],
    "EVALUATION_RUN_ID": os.environ["EVALUATION_RUN_ID"],
    "MODEL_VERSION": os.environ["MODEL_VERSION"]
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

IMAGE=$(aws ecs describe-task-definition \
  --task-definition "$PACKAGER_TASK_DEF_ARN" \
  --query 'taskDefinition.containerDefinitions[0].image' \
  --output text)

EXPECTED_IMAGE_TAG="${PACKAGE_IMAGE_TAG:-p001}"

if [[ "$IMAGE" != *"/bikepulse/trainer:${EXPECTED_IMAGE_TAG}" ]]
then
  echo "ERROR: Unexpected Packager image."
  echo "Actual: $IMAGE"
  exit 1
fi

echo "=========================================="
echo "BIKEPULSE MODEL PACKAGE"
echo "=========================================="
echo "Mode: $MODE"
echo "Selection ID: $RUN_SELECTION_ID"
echo "Evaluation ID: $EVALUATION_RUN_ID"
echo "Model Version: $MODEL_VERSION"

RESPONSE=$(aws ecs run-task \
  --cluster "$ECS_CLUSTER" \
  --task-definition "$PACKAGER_TASK_DEF_ARN" \
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

FINISHED="false"

for ATTEMPT in {1..90}
do
  STATE=$(aws ecs describe-tasks \
    --cluster "$ECS_CLUSTER" \
    --tasks "$TASK_ARN" \
    --query 'tasks[0].lastStatus' \
    --output text)

  echo "Attempt=$ATTEMPT State=$STATE"

  if [[ "$STATE" == "STOPPED" ]]
  then
    FINISHED="true"
    break
  fi

  sleep 10
done

if [[ "$FINISHED" != "true" ]]
then
  echo "PACKAGE TASK WAIT TIMEOUT"
  echo "Inspect Task: $TASK_ARN"
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
  | jq -r '.tasks[0].stoppedReason // "none"')

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

if [[ "$MODE" == "bad" ]]
then
  if [[ "$EXIT_CODE" == "1" ]]
  then
    echo "INVALID SELECTION REJECTION: PASSED"
    exit 0
  else
    echo "INVALID SELECTION REJECTION: FAILED"
    exit 1
  fi
fi

if [[ "$EXIT_CODE" != "0" ]]
then
  echo "MODEL PACKAGING: FAILED"
  exit 1
fi

aws s3 cp \
  "s3://${BIKEPULSE_BUCKET}/${PACKAGE_PREFIX}metadata.json" \
  data/package/executions/metadata.json

aws s3 cp \
  "s3://${BIKEPULSE_BUCKET}/${PACKAGE_PREFIX}package_manifest.json" \
  data/package/executions/package_manifest.json

jq -e \
  --arg version "$MODEL_VERSION" '
    .status == "CANDIDATE_PENDING_REGISTRATION"
    and .model_version == $version
    and .feature_count == 19
    and .model_retrained_for_packaging == false
  ' data/package/executions/metadata.json \
  > /dev/null

jq -e \
  --arg version "$MODEL_VERSION" '
    .status == "PASSED"
    and .model_version == $version
    and (.files | length) == 9
  ' data/package/executions/package_manifest.json \
  > /dev/null

echo ""
echo "[Packaged Model]"

jq '{
  model_id,
  model_version,
  model_name,
  feature_version,
  feature_count,
  model_sha256,
  validation_mae,
  test_metrics
}' data/package/executions/metadata.json

echo ""
echo "MODEL PACKAGE ECS TEST: PASSED"
echo "S3 PREFIX: $PACKAGE_PREFIX"
echo "=========================================="
