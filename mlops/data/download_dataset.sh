#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

DATASET_URL="https://archive.ics.uci.edu/static/public/560/seoul+bike+sharing+demand.zip"
DOWNLOAD_DIR="/tmp/bikepulse-g"
ZIP_FILE="${DOWNLOAD_DIR}/uci560.zip"
CSV_FILE="data/raw/SeoulBikeData.csv"

mkdir -p "$DOWNLOAD_DIR"
mkdir -p data/raw

echo "=========================================="
echo "BIKEPULSE DATASET DOWNLOAD"
echo "=========================================="

if [[ -f "$CSV_FILE" ]]
then
 echo "Dataset already exists: $CSV_FILE"
 echo "Existing raw file will not be overwritten."
 exit 0
fi

echo "[A] Downloading official UCI dataset..."
curl -fL \
 --retry 3 \
 --retry-delay 2 \
 --connect-timeout 20 \
 "$DATASET_URL" \
 -o "$ZIP_FILE"

echo "[B] Checking ZIP archive..."
unzip -t "$ZIP_FILE" > /dev/null
echo "ZIP VALIDATION: PASSED"

echo "[C] Finding SeoulBikeData.csv..."
CSV_MEMBER=$(
 unzip -Z1 "$ZIP_FILE" |
 grep -E '(^|/)SeoulBikeData\.csv$' |
 head -n 1
)

if [[ -z "$CSV_MEMBER" ]]
then
 echo "ERROR: SeoulBikeData.csv not found in ZIP."
 exit 1
fi

echo "CSV MEMBER: $CSV_MEMBER"

echo "[D] Extracting original CSV..."
unzip -p \
 "$ZIP_FILE" \
 "$CSV_MEMBER" \
 > "$CSV_FILE"

test -s "$CSV_FILE"
echo "CSV EXTRACTION: PASSED"

echo "[E] File details..."
ls -lh "$CSV_FILE"

echo ""
echo "SHA256:"
sha256sum "$CSV_FILE"

echo ""
echo "=========================================="
echo "RESULT: DOWNLOAD COMPLETED"
echo "=========================================="
