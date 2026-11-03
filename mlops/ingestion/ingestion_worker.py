import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
import boto3
import pandas as pd
from botocore.exceptions import ClientError

PREFIX = (
 "raw/incoming/source=uci-seoul-bike/"
 "version=dv002/"
)
REPORT_ROOT = (
 "monitoring/ingestion/version=dv002/"
)
MAX_BYTES = 10 * 1024 * 1024

def now():
 return datetime.now(
 timezone.utc
 ).isoformat()

def require(condition, message):
 if not condition:
  raise ValueError(message)

def sha256(data):
 return hashlib.sha256(
  data
 ).hexdigest()

def json_bytes(data):
 return (
  json.dumps(
   data,
   indent=2,
   ensure_ascii=False,
   allow_nan=False
  ) + "\n"
 ).encode("utf-8")

def event_log(name, **fields):
 print(
  json.dumps({
   "event": name,
   "timestamp": now(),
   **fields
  }, ensure_ascii=False),
  flush=True
 )

def validate_event(event, expected_bucket):
 require(
  event.get("source") == "aws.s3"
  and event.get("detail-type") == "Object Created",
  "UNSUPPORTED_EVENT_TYPE"
 )
 detail = event.get("detail") or {}
 bucket = (
  detail.get("bucket") or {}
 ).get("name")
 obj = detail.get("object") or {}
 key = obj.get("key")
 version_id = obj.get("version-id")
 require(
  bucket == expected_bucket,
  "UNEXPECTED_S3_BUCKET"
 )
 require(
  isinstance(key, str)
  and re.fullmatch(
   re.escape(PREFIX)
   + r"[A-Za-z0-9_.-]+\.csv",
   key
  ) is not None,
  "UNEXPECTED_OBJECT_KEY"
 )
 require(
  isinstance(version_id, str)
  and len(version_id) > 0
  and version_id != "null",
  "S3_VERSION_ID_REQUIRED"
 )
 size = obj.get("size")
 if size is not None:
  require(
   0 < int(size) <= MAX_BYTES,
   "OBJECT_SIZE_NOT_ALLOWED"
  )
 return bucket, key, version_id

def validate_csv(content):
 require(
  0 < len(content) <= MAX_BYTES,
  "CSV_FILE_SIZE_INVALID"
 )
 df = None
 for encoding in (
  "cp949",
  "utf-8-sig"
 ):
  try:
   df = pd.read_csv(
    io.BytesIO(content),
    encoding=encoding
   )
   break
  except UnicodeDecodeError:
   continue
 require(
  df is not None,
  "CSV_ENCODING_UNSUPPORTED"
 )
 require(
  df.shape == (8760, 14),
  "CSV_SHAPE_MISMATCH"
 )
 required = {
  "Date",
  "Hour",
  "Rented Bike Count",
  "Functioning Day"
 }
 require(
  required.issubset(df.columns),
  "CSV_REQUIRED_COLUMNS_MISSING"
 )
 dates = pd.to_datetime(
  df["Date"],
  format="%d/%m/%Y",
  errors="raise"
 )
 hours = pd.to_numeric(
  df["Hour"],
  errors="coerce"
 )
 rentals = pd.to_numeric(
  df["Rented Bike Count"],
  errors="coerce"
 )
 require(
  hours.notna().all()
  and hours.between(0, 23).all()
  and (hours % 1 == 0).all(),
  "CSV_HOUR_INVALID"
 )
 require(
  rentals.notna().all()
  and (rentals >= 0).all(),
  "CSV_TARGET_INVALID"
 )
 require(
  df["Functioning Day"].isin(
   ["Yes", "No"]
  ).all(),
  "CSV_FUNCTIONING_INVALID"
 )
 timestamps = dates + pd.to_timedelta(
  hours,
  unit="h"
 )
 require(
  timestamps.is_unique,
  "CSV_DUPLICATE_TIMESTAMP"
 )
 return {
  "rows": len(df),
  "columns": len(df.columns),
  "timestamp_min": (
   timestamps.min().isoformat()
  ),
  "timestamp_max": (
   timestamps.max().isoformat()
  ),
  "null_cells": int(
   df.isna().sum().sum()
  ),
  "functioning_rows": int(
   df["Functioning Day"].eq("Yes").sum()
  ),
  "non_functioning_rows": int(
   df["Functioning Day"].eq("No").sum()
  )
 }

def main():
 event = json.loads(
  os.environ["S3_EVENT_JSON"]
 )
 expected_bucket = os.environ[
  "BIKEPULSE_BUCKET"
 ]
 table_name = os.environ[
  "INGESTION_TABLE"
 ]
 bucket, key, version_id = validate_event(
  event,
  expected_bucket
 )
 object_id = sha256(
  (
   bucket + "\n"
   + key + "\n"
   + version_id
  ).encode("utf-8")
 )
 report_key = (
  REPORT_ROOT
  + f"object_id={object_id}/report.json"
 )
 s3 = boto3.client("s3")
 table = boto3.resource(
  "dynamodb"
 ).Table(table_name)
 
 # Claim this precise S3 object version once.
 try:
  table.put_item(
   Item={
    "object_id": object_id,
    "bucket": bucket,
    "object_key": key,
    "object_version_id": version_id,
    "status": "IN_PROGRESS",
    "report_key": report_key,
    "event_id": event.get("id", ""),
    "created_at": now(),
    "updated_at": now()
   },
   ConditionExpression=(
    "attribute_not_exists(object_id)"
   )
  )
 except ClientError as exc:
  code = exc.response[
   "Error"
  ]["Code"]
  if code != "ConditionalCheckFailedException":
   raise
  prior = table.get_item(
   Key={
    "object_id": object_id
   },
   ConsistentRead=True
  ).get("Item", {})
  event_log(
   "INGESTION_DUPLICATE_SKIPPED",
   object_id=object_id,
   original_status=prior.get(
    "status", "UNKNOWN"
   ),
   object_key=key
  )
  return 0

 event_log(
  "INGESTION_CLAIMED",
  object_id=object_id,
  object_key=key,
  version_id=version_id
 )
 report = {
  "object_id": object_id,
  "source_bucket": bucket,
  "source_key": key,
  "source_version_id": version_id,
  "source_event_id": event.get("id"),
  "dataset_version": "dv002",
  "validated_at": now()
 }
 status = "FAILED"
 error = None
 try:
  response = s3.get_object(
   Bucket=bucket,
   Key=key,
   VersionId=version_id
  )
  content = response["Body"].read(
   MAX_BYTES + 1
  )
  check = validate_csv(
   content
  )
  report.update({
   "status": "PASSED",
   "source_sha256": sha256(content),
   "quality": check
  })
  status = "PASSED"
 except Exception as exc:
  error = (
   f"{type(exc).__name__}: {exc}"
  )
  report.update({
   "status": "FAILED",
   "error": error
  })

 # Persist the result before closing the ledger.
 s3.put_object(
  Bucket=bucket,
  Key=report_key,
  Body=json_bytes(report),
  ContentType="application/json",
  IfNoneMatch="*"
 )
 table.update_item(
  Key={
   "object_id": object_id
  },
  UpdateExpression=(
   "SET #s = :status, "
   "updated_at = :updated"
  ),
  ConditionExpression=(
   "#s = :in_progress"
  ),
  ExpressionAttributeNames={
   "#s": "status"
  },
  ExpressionAttributeValues={
   ":status": status,
   ":updated": now(),
   ":in_progress": "IN_PROGRESS"
  }
 )
 if status == "PASSED":
  event_log(
   "INGESTION_VALIDATION_PASSED",
   object_id=object_id,
   object_key=key,
   report_key=report_key,
   rows=report["quality"]["rows"]
  )
  return 0
 event_log(
  "INGESTION_VALIDATION_FAILED",
  object_id=object_id,
  object_key=key,
  report_key=report_key,
  error=error
 )
 return 1

if __name__ == "__main__":
 try:
  sys.exit(main())
 except Exception as exc:
  event_log(
   "INGESTION_WORKER_ERROR",
   error=(
    f"{type(exc).__name__}: {exc}"
   )
  )
  sys.exit(1)
