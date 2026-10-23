#!/usr/bin/env bash
set -euo pipefail

REPOSITORIES=(
  "bikepulse/frontend"
  "bikepulse/api"
  "bikepulse/processor"
  "bikepulse/trainer"
  "bikepulse/monitor"
)

for REPOSITORY in "${REPOSITORIES[@]}"
do
  echo "Checking ECR: $REPOSITORY"
  if aws ecr describe-repositories \
    --repository-names "$REPOSITORY" \
    > /dev/null 2>&1
  then
    echo "EXISTS: $REPOSITORY"
  else
    aws ecr create-repository \
      --repository-name "$REPOSITORY" \
      --image-tag-mutability IMMUTABLE \
      --encryption-configuration encryptionType=AES256 \
      > /dev/null
    echo "CREATED: $REPOSITORY"
  fi
done

echo "ECR REPOSITORIES READY"
