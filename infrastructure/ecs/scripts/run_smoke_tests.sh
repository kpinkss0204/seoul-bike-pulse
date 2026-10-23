#!/usr/bin/env bash
set -euo pipefail

: "${ECS_CLUSTER:?ECS_CLUSTER is required}"
: "${PRIVATE_A:?PRIVATE_A is required}"
: "${PRIVATE_C:?PRIVATE_C is required}"
: "${SG_FRONTEND:?SG_FRONTEND is required}"
: "${SG_API:?SG_API is required}"
: "${SG_MLOPS:?SG_MLOPS is required}"

run_test() {
 local COMPONENT="$1"
 local SUBNET="$2"
 local SECURITY_GROUP="$3"
 local FAMILY="bikepulse-dev-${COMPONENT}-task"

 echo ""
 echo "=========================================="
 echo "RUNNING: $COMPONENT"
 echo "=========================================="

 local OVERRIDES='{}'
 if [[ "$COMPONENT" == "api" ]]
 then
 OVERRIDES='{
 "containerOverrides": [
 {
 "name": "api",
 "command": [
 "python",
 "-m",
 "app.smoke"
 ]
 }
 ]
 }'
 elif [[ "$COMPONENT" == "frontend" ]]
 then
 OVERRIDES='{
 "containerOverrides": [
 {
 "name": "frontend",
 "command": [
 "/bin/sh",
 "-c",
 "nginx && wget -q -O /dev/null http://127.0.0.1/ && echo FRONTEND_SMOKE_PASSED"
 ]
 }
 ]
 }'
 fi

 local RESPONSE
 RESPONSE=$(aws ecs run-task \
 --cluster "$ECS_CLUSTER" \
 --task-definition "$FAMILY" \
 --launch-type FARGATE \
 --platform-version LATEST \
 --count 1 \
 --network-configuration \
 "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SECURITY_GROUP],assignPublicIp=DISABLED}" \
 --overrides "$OVERRIDES" \
 --output json)

 echo "$RESPONSE" | jq -e '
 (.failures | length == 0)
 and
 (.tasks | length == 1)
 ' > /dev/null || {
 echo "$RESPONSE" | jq .
 echo "TASK START FAILED: $COMPONENT"
 return 1
 }

 local TASK_ARN
 TASK_ARN=$(echo "$RESPONSE" \
 | jq -r '.tasks[0].taskArn')
 echo "TASK ARN: $TASK_ARN"

 echo "Waiting for task completion..."
 aws ecs wait tasks-stopped \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN"

 local EXIT_CODE
 EXIT_CODE=$(aws ecs describe-tasks \
 --cluster "$ECS_CLUSTER" \
 --tasks "$TASK_ARN" \
 --query 'tasks[0].containers[0].exitCode' \
 --output text)
 echo "EXIT CODE: $EXIT_CODE"

 local LOG_GROUP="/ecs/bikepulse-dev/${COMPONENT}"
 local TASK_ID="${TASK_ARN##*/}"
 local LOG_STREAM="ecs/${COMPONENT}/${TASK_ID}"

 echo ""
 echo "CloudWatch log stream:"
 echo "$LOG_STREAM"

 sleep 5
 aws logs get-log-events \
 --log-group-name "$LOG_GROUP" \
 --log-stream-name "$LOG_STREAM" \
 --query 'events[].message' \
 --output text || true

 if [[ "$EXIT_CODE" != "0" ]]
 then
 echo "SMOKE TEST FAILED: $COMPONENT"
 return 1
 fi

 echo "SMOKE TEST PASSED: $COMPONENT"
}

run_test processor "$PRIVATE_A" "$SG_MLOPS"
run_test trainer "$PRIVATE_A" "$SG_MLOPS"
run_test monitor "$PRIVATE_C" "$SG_MLOPS"
run_test api "$PRIVATE_A" "$SG_API"
run_test frontend "$PRIVATE_C" "$SG_FRONTEND"

echo ""
echo "=========================================="
echo "ALL ECS SMOKE TESTS PASSED"
echo "=========================================="
