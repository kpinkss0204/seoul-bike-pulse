#!/usr/bin/env bash
set -euo pipefail

: "${BIKEPULSE_BUCKET:?BIKEPULSE_BUCKET is required}"
: "${AWS_REGION:?AWS_REGION is required}"

echo "=============================================="
echo "BIKEPULSE S3 DATA LAKE VERIFICATION"
echo "=============================================="
echo ""

echo "[A] Bucket existence"
aws s3api head-bucket \
  --bucket "$BIKEPULSE_BUCKET"
echo "BUCKET: PASSED"
echo ""

echo "[B] Bucket region"
ACTUAL_REGION=$(aws s3api get-bucket-location \
  --bucket "$BIKEPULSE_BUCKET" \
  --query LocationConstraint \
  --output text)
test "$ACTUAL_REGION" = "$AWS_REGION"
echo "REGION: PASSED ($ACTUAL_REGION)"
echo ""

echo "[C] Bucket versioning"
VERSIONING=$(aws s3api get-bucket-versioning \
  --bucket "$BIKEPULSE_BUCKET" \
  --query Status \
  --output text)
test "$VERSIONING" = "Enabled"
echo "VERSIONING: PASSED"
echo ""

echo "[D] Default encryption"
ENCRYPTION=$(aws s3api get-bucket-encryption \
  --bucket "$BIKEPULSE_BUCKET" \
  --query \
  'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
  --output text)
test "$ENCRYPTION" = "AES256"
echo "ENCRYPTION: PASSED"
echo ""

echo "[E] Public access block"
PUBLIC_ACCESS=$(aws s3api get-public-access-block \
  --bucket "$BIKEPULSE_BUCKET" \
  --output json)
echo "$PUBLIC_ACCESS" | jq -e '
  .PublicAccessBlockConfiguration
  | .BlockPublicAcls == true
  and .IgnorePublicAcls == true
  and .BlockPublicPolicy == true
  and .RestrictPublicBuckets == true
' > /dev/null
echo "PUBLIC ACCESS BLOCK: PASSED"
echo ""

echo "[F] Required prefixes"
PREFIXES=(
  "raw/"
  "curated/"
  "features/"
  "splits/"
  "models/"
  "monitoring/"
  "prediction-logs/"
  "athena-results/"
  "system/"
)

for PREFIX in "${PREFIXES[@]}"
do
  aws s3api head-object \
    --bucket "$BIKEPULSE_BUCKET" \
    --key "$PREFIX" \
    > /dev/null
  echo "PREFIX OK: $PREFIX"
done
echo ""

echo "[G] HTTPS bucket policy"
BUCKET_POLICY=$(aws s3api get-bucket-policy \
  --bucket "$BIKEPULSE_BUCKET" \
  --query Policy \
  --output text)
echo "$BUCKET_POLICY" | jq -e '
  .Statement
  | any(
    .Sid == "DenyInsecureTransport"
    and .Effect == "Deny"
    and .Condition.Bool["aws:SecureTransport"] == "false"
  )
' > /dev/null
echo "HTTPS POLICY: PASSED"
echo ""

echo "[H] Lifecycle configuration"
LIFECYCLE=$(aws s3api get-bucket-lifecycle-configuration \
  --bucket "$BIKEPULSE_BUCKET" \
  --output json)
echo "$LIFECYCLE" | jq -e '
  .Rules
  | any(
    .ID == "bikepulse-abort-incomplete-multipart-7d"
    and .Status == "Enabled"
  )
' > /dev/null
echo "$LIFECYCLE" | jq -e '
  .Rules
  | any(
    .ID == "bikepulse-smoke-expire-7d"
    and .Status == "Enabled"
    and .Filter.Prefix == "system/healthchecks/"
  )
' > /dev/null
echo "LIFECYCLE: PASSED"
echo ""

echo "=============================================="
echo "RESULT: ALL S3 CONFIGURATION CHECKS PASSED"
echo "=============================================="
