#!/usr/bin/env bash
set -euo pipefail
: "${GLUE_CRAWLER:?GLUE_CRAWLER is required}"
echo "Waiting for crawler: $GLUE_CRAWLER"
sleep 10
for ATTEMPT in {1..60}
do
 RESULT=$(aws glue get-crawler \
 --name "$GLUE_CRAWLER" \
 --output json)
 STATE=$(echo "$RESULT" \
 | jq -r '.Crawler.State')
 LAST_STATUS=$(echo "$RESULT" \
 | jq -r '.Crawler.LastCrawl.Status // "NONE"')
 echo "Attempt=$ATTEMPT State=$STATE Last=$LAST_STATUS"
 if [[ "$STATE" == "READY" ]]
 then
 if [[ "$LAST_STATUS" == "SUCCEEDED" ]]
 then
 echo "GLUE CRAWLER: PASSED"
 exit 0
 fi
 if [[ "$LAST_STATUS" == "FAILED" ||
 "$LAST_STATUS" == "CANCELLED" ]]
 then
 echo "$RESULT" | jq '.Crawler.LastCrawl'
 echo "GLUE CRAWLER: FAILED"
 exit 1
 fi
 fi
 sleep 10
done
echo "GLUE CRAWLER: TIMEOUT"
exit 1
