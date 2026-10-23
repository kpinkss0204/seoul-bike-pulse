#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../.."

TRUST_DIR="infrastructure/iam/trust"
POLICY_DIR="infrastructure/iam/policies"

ROLES=(
  bikepulse-dev-ecs-execution-role
  bikepulse-dev-frontend-task-role
  bikepulse-dev-api-task-role
  bikepulse-dev-processor-task-role
  bikepulse-dev-trainer-task-role
  bikepulse-dev-monitor-task-role
  bikepulse-dev-sfn-role
  bikepulse-dev-eventbridge-start-role
  bikepulse-dev-scheduler-start-role
)

echo "=========================================="
echo "BIKEPULSE IAM ROLE DEPLOYMENT"
echo "=========================================="

for ROLE in "${ROLES[@]}"
do
  echo ""
  echo "Processing Role: $ROLE"
  TRUST_FILE="${TRUST_DIR}/${ROLE}.json"
  POLICY_FILE="${POLICY_DIR}/${ROLE}.json"

  if aws iam get-role \
    --role-name "$ROLE" \
    > /dev/null 2>&1
  then
    echo "Role already exists: $ROLE"
    aws iam update-assume-role-policy \
      --role-name "$ROLE" \
      --policy-document "file://${TRUST_FILE}"
    echo "Trust policy updated"
  else
    aws iam create-role \
      --role-name "$ROLE" \
      --assume-role-policy-document "file://${TRUST_FILE}" \
      --description "BIKEPULSE project IAM role" \
      > /dev/null
    echo "Role created: $ROLE"
  fi

  if [[ "$ROLE" == "bikepulse-dev-ecs-execution-role" ]]
  then
    aws iam attach-role-policy \
      --role-name "$ROLE" \
      --policy-arn \
      arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
    echo "AWS managed execution policy attached"
  elif [[ -f "$POLICY_FILE" ]]
  then
    aws iam put-role-policy \
      --role-name "$ROLE" \
      --policy-name "${ROLE}-policy" \
      --policy-document "file://${POLICY_FILE}"
    echo "Inline policy attached"
  else
    echo "No AWS API permissions required"
  fi
done

echo ""
echo "=========================================="
echo "RESULT: IAM ROLE DEPLOYMENT COMPLETED"
echo "=========================================="
