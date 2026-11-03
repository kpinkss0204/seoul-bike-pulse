#!/usr/bin/env bash
set -euo pipefail
EXECUTION_ARN="${1:?Execution ARN required}"
echo "Monitoring Step Functions execution:"
echo "$EXECUTION_ARN"
for ATTEMPT in $(seq 1 720)
do
 STATUS=$(aws stepfunctions describe-execution \
 --execution-arn "$EXECUTION_ARN" \
 --query status \
 --output text)
 echo "Attempt=$ATTEMPT Status=$STATUS"
 case "$STATUS" in
 SUCCEEDED)
 echo "PIPELINE EXECUTION: SUCCEEDED"
 exit 0
 ;;
 FAILED|TIMED_OUT|ABORTED)
 aws stepfunctions describe-execution \
 --execution-arn "$EXECUTION_ARN" \
 --query '{
 Status:status,
 Error:error,
 Cause:cause
 }' \
 --output json
 echo "PIPELINE EXECUTION: FAILED"
 exit 1
 ;;
 RUNNING)
 sleep 30
 ;;
 *)
 echo "Unexpected execution status: $STATUS"
 exit 1
 ;;
 esac
done
echo "MONITORING TIMEOUT"
echo "Execution may still be running:"
echo "$EXECUTION_ARN"
exit 1
