#!/usr/bin/env bash
set -euo pipefail
: "${ECS_CLUSTER:?Missing ECS_CLUSTER}"
: "${BIKEPULSE_BUCKET:?Missing BIKEPULSE_BUCKET}"
: "${REGISTRY_TABLE:?Missing REGISTRY_TABLE}"
: "${REGISTRY_TASK_DEF_ARN:?Missing REGISTRY_TASK_DEF_ARN}"
: "${MODEL_VERSION:?Missing MODEL_VERSION}"
: "${PRIVATE_A:?Missing PRIVATE_A}"
: "${SG_MLOPS:?Missing SG_MLOPS}"

mkdir -p data/registry/executions

IMAGE=$(aws ecs describe-task-definition \
 --task-definition "$REGISTRY_TASK_DEF_ARN" \
 --query 'taskDefinition.containerDefinitions[0].image' \
 --output text)

EXPECTED_TAG="${REGISTRY_IMAGE_TAG:-q001}"

if [[ "$IMAGE" != *"/bikepulse/trainer:${EXPECTED_TAG}" ]]
then
 echo "ERROR: Unexpected Registry Worker image."
 echo "Actual: $IMAGE"
 exit 1
fi

OVERRIDES_FILE=$(mktemp)
trap 'rm -f "$OVERRIDES_FILE"' EXIT

python3 - "$OVERRIDES_FILE" <<'PY'
import json
import os
import sys

names = [
 "BIKEPULSE_BUCKET",
 "REGISTRY_TABLE",
 "MODEL_VERSION"
]

overrides = {
 "containerOverrides": [
 {
 "name": "trainer",
 "command": [
 "--action",
 "register"
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
 json.dump(overrides, f)
PY

echo "=========================================="
echo "BIKEPULSE MODEL REGISTRY REGISTRATION"
echo "=========================================="
echo "Table: $REGISTRY_TABLE"
echo "Model: bike-demand"
echo "Version: $MODEL_VERSION"

RESPONSE=$(aws ecs run-task \
 --cluster "$ECS_CLUSTER" \
 --task-definition "$REGISTRY_TASK_DEF_ARN" \
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
 > data/registry/executions/registration_ecs.json

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

if [[ "$EXIT_CODE" != "0" ]]
then
 echo "MODEL REGISTRATION: FAILED"
 exit 1
fi

echo ""
echo "[DynamoDB Registry Item]"
aws dynamodb get-item \
 --table-name "$REGISTRY_TABLE" \
 --key "{\"model_id\":{\"S\":\"bike-demand\"},\"model_version\":{\"S\":\"${MODEL_VERSION}\"}}" \
 --consistent-read \
 --output json \
 > data/registry/executions/registered_model.json

jq -e \
 --arg version "$MODEL_VERSION" \
 '.Item.model_id.S == "bike-demand"
 and .Item.model_version.S == $version
 and .Item.status.S == "CANDIDATE"
 and .Item.item_type.S == "MODEL"
 and .Item.feature_count.N == "19"
 and .Item.approval_status.S == "PENDING"
 and .Item.deployment_status.S == "NOT_DEPLOYED"' \
 data/registry/executions/registered_model.json \
 > /dev/null

echo "MODEL REGISTRATION TEST: PASSED"
echo "=========================================="
