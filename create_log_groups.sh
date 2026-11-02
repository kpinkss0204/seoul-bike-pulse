#!/usr/bin/env bash
set -euo pipefail

LOG_GROUPS=(
  frontend
  api
  processor
  trainer
  monitor
)

for COMPONENT in "${LOG_GROUPS[@]}"
do
  GROUP="/ecs/bikepulse-dev/${COMPONENT}"

  if ! aws logs describe-log-groups --log-group-name-prefix "$GROUP" --query 'logGroups[].logGroupName' --output text | tr '\t' '\n' | grep -Fxq "$GROUP"; then
    aws logs create-log-group --log-group-name "$GROUP"
  fi

  aws logs put-retention-policy --log-group-name "$GROUP" --retention-in-days 7

  echo "LOG GROUP READY: $GROUP"
done
