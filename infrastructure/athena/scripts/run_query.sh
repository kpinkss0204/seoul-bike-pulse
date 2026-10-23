#!/usr/bin/env bash
set -euo pipefail

SQL_FILE="${1:?Usage: run_query.sh <sql_file>}"
: "${GLUE_DATABASE:?Missing GLUE_DATABASE}"
: "${GLUE_TABLE:?Missing GLUE_TABLE}"
: "${ATHENA_WORKGROUP:?Missing ATHENA_WORKGROUP}"

# --------------------------------------------------
# 1. SQL 파일 확인
# --------------------------------------------------
if [[ ! -f "$SQL_FILE" ]]
then
 echo "SQL file not found: $SQL_FILE"
 exit 1
fi

# --------------------------------------------------
# 2. SQL 파일 읽기 및 테이블 이름 치환
# --------------------------------------------------
SQL=$(sed "s/__TABLE__/${GLUE_TABLE}/g" "$SQL_FILE")

echo "=========================================="
echo "BIKEPULSE ATHENA QUERY"
echo "=========================================="
echo "SQL file: $SQL_FILE"
echo "Table: ${GLUE_DATABASE}.${GLUE_TABLE}"
echo ""

# --------------------------------------------------
# 3. Athena 쿼리 실행
# --------------------------------------------------
QUERY_ID=$(aws athena start-query-execution \
 --query-string "$SQL" \
 --work-group "$ATHENA_WORKGROUP" \
 --query-execution-context \
 "Database=${GLUE_DATABASE},Catalog=AwsDataCatalog" \
 --query QueryExecutionId \
 --output text)

echo "Query ID: $QUERY_ID"

# --------------------------------------------------
# 4. 쿼리 실행 상태 확인
# --------------------------------------------------
for ATTEMPT in {1..60}
do
 DETAIL=$(aws athena get-query-execution \
 --query-execution-id "$QUERY_ID" \
 --output json)
 
 STATE=$(echo "$DETAIL" | jq -r '.QueryExecution.Status.State')
 echo "Attempt=$ATTEMPT State=$STATE"

 # ------------------------------------------------
 # 5. 쿼리 성공
 # ------------------------------------------------
 if [[ "$STATE" == "SUCCEEDED" ]]
 then
 echo ""
 echo "=========================================="
 echo "ATHENA QUERY RESULTS"
 echo "=========================================="
 echo ""

 # ----------------------------------------------
 # 6. Athena 결과를 정렬된 표 형태로 출력
 # ----------------------------------------------
 aws athena get-query-results \
 --query-execution-id "$QUERY_ID" \
 --output json |
 python3 -c '
import json
import sys
import unicodedata

payload = json.load(sys.stdin)
result_set = payload.get("ResultSet", {})

column_info = result_set.get("ResultSetMetadata", {}).get("ColumnInfo", [])
headers = [
 column.get("Label") or column.get("Name", "")
 for column in column_info
]

rows = []
for entry in result_set.get("Rows", []):
 values = [
 datum.get("VarCharValue", "")
 for datum in entry.get("Data", [])
 ]
 if len(values) < len(headers):
     values.extend([""] * (len(headers) - len(values)))
 rows.append(values[:len(headers)])

if rows and rows[0] == headers:
 rows.pop(0)

if not headers:
 print("No columns returned.")
 sys.exit(0)

def clean_text(value):
 text = str(value)
 text = text.replace("\n", " ")
 text = text.replace("\r", " ")
 text = text.replace("\t", " ")
 return text

headers = [clean_text(value) for value in headers]
rows = [[clean_text(value) for value in row] for row in rows]

def display_width(text):
 width = 0
 for char in text:
     if unicodedata.east_asian_width(char) in ("F", "W"):
         width += 2
     else:
         width += 1
 return width

table = [headers] + rows
widths = [
 max(display_width(row[i]) for row in table)
 for i in range(len(headers))
]

def print_row(row):
 cells = []
 for i, value in enumerate(row):
     padding = widths[i] - display_width(value)
     cells.append(value + (" " * padding))
 print(" ".join(cells).rstrip())

print_row(headers)
print(" ".join("-" * width for width in widths))

for row in rows:
     print_row(row)

print()
print(f"TOTAL ROWS: {len(rows)}")
'

 # ----------------------------------------------
 # 7. 스캔 데이터 크기 확인
 # ----------------------------------------------
 SCANNED=$(echo "$DETAIL" | jq -r '.QueryExecution.Statistics.DataScannedInBytes // 0')
 echo ""
 echo "=========================================="
 echo "ATHENA QUERY SUMMARY"
 echo "=========================================="
 echo "QUERY ID: $QUERY_ID"
 echo "BYTES SCANNED: $SCANNED"
 echo "ATHENA QUERY: PASSED"
 exit 0
 fi

 # ------------------------------------------------
 # 8. 쿼리 실패 또는 취소
 # ------------------------------------------------
 if [[ "$STATE" == "FAILED" || "$STATE" == "CANCELLED" ]]
 then
 echo ""
 echo "ATHENA QUERY ERROR"
 echo "$DETAIL" | jq '.QueryExecution.Status'
 echo "ATHENA QUERY: FAILED"
 exit 1
 fi

 sleep 3
done

echo "ATHENA QUERY: TIMEOUT"
exit 1
