import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.cwd()
CONTRACT_PATH = ROOT / "config/project_contract.json"

EXPECTED_HEADERS = [
    "date",
    "rentedbikecount",
    "hour",
    "temperature",
    "humidity",
    "windspeed",
    "visibility",
    "dewpointtemperature",
    "solarradiation",
    "rainfall",
    "snowfall",
    "seasons",
    "holiday",
    "functioningday"
]

ALLOWED_SEASONS = {
    "Winter", "Spring", "Summer", "Autumn"
}
ALLOWED_HOLIDAYS = {
    "Holiday", "No Holiday"
}
ALLOWED_FUNCTIONING = {
    "Yes", "No"
}

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def sha256_hex(content):
    return hashlib.sha256(content).hexdigest()

def normalize_header(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())

def add_error(errors, message):
    errors.append(message)

def decode_csv(content):
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("CSV encoding is not UTF-8-SIG or CP949")

def validate_dataset(csv_bytes, manifest, contract, raw_key):
    errors = []
    dataset = contract["dataset"]
    training = contract["training"]
    expected_rows = dataset["expected_rows"]
    expected_columns = dataset["expected_columns"]
    actual_sha = sha256_hex(csv_bytes)
    
    if manifest.get("sha256") != actual_sha:
        add_error(errors, "SHA256_MISMATCH")
    if manifest.get("raw_s3_key") != raw_key:
        add_error(errors, "MANIFEST_RAW_KEY_MISMATCH")
    if manifest.get("dataset_version") != dataset["version"]:
        add_error(errors, "DATASET_VERSION_MISMATCH")
    if manifest.get("file_size_bytes") != len(csv_bytes):
        add_error(errors, "FILE_SIZE_MISMATCH")
    if manifest.get("row_count") != expected_rows:
        add_error(errors, "MANIFEST_ROW_COUNT_MISMATCH")
    if manifest.get("column_count") != expected_columns:
        add_error(errors, "MANIFEST_COLUMN_COUNT_MISMATCH")
        
    text, encoding = decode_csv(csv_bytes)
    rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not rows:
        raise ValueError("EMPTY_CSV")
        
    headers = rows[0]
    records = rows[1:]
    
    if len(headers) != expected_columns:
        add_error(errors, f"COLUMN_COUNT: {len(headers)}")
    if len(records) != expected_rows:
        add_error(errors, f"ROW_COUNT: {len(records)}")
        
    if len(headers) == len(EXPECTED_HEADERS):
        for index, (actual, expected) in enumerate(zip(headers, EXPECTED_HEADERS), start=1):
            if not normalize_header(actual).startswith(expected):
                add_error(errors, f"HEADER_{index}: {actual}")
                
    timestamps = set()
    hours_by_date = defaultdict(set)
    functioning_counts = Counter()
    valid_rented_values = []
    duplicate_count = 0
    missing_field_rows = 0
    invalid_row_count = 0
    sample_issues = []
    all_dates = []
    
    for line_number, row in enumerate(records, start=2):
        try:
            if len(row) != expected_columns:
                raise ValueError(f"column count={len(row)}")
            if any(not value.strip() for value in row):
                missing_field_rows += 1
                raise ValueError("missing required value")
                
            record_date = datetime.strptime(row[0].strip(), dataset["date_format"]).date()
            rented = int(row[1])
            hour = int(row[2])
            temperature = float(row[3])
            humidity = int(row[4])
            wind = float(row[5])
            visibility = float(row[6])
            dew_point = float(row[7])
            solar = float(row[8])
            rainfall = float(row[9])
            snowfall = float(row[10])
            season = row[11].strip()
            holiday = row[12].strip()
            functioning = row[13].strip()
            
            if rented < 0:
                raise ValueError("negative rented bike count")
            if not 0 <= hour <= 23:
                raise ValueError(f"invalid hour={hour}")
            if not 0 <= humidity <= 100:
                raise ValueError(f"invalid humidity={humidity}")
                
            numeric_values = [
                temperature,
                float(humidity),
                wind,
                visibility,
                dew_point,
                solar,
                rainfall,
                snowfall
            ]
            if not all(math.isfinite(x) for x in numeric_values):
                raise ValueError("non-finite numeric value")
            if any(x < 0 for x in (wind, visibility, solar, rainfall, snowfall)):
                raise ValueError("negative nonnegative weather feature")
                
            if season not in ALLOWED_SEASONS:
                raise ValueError(f"invalid season={season}")
            if holiday not in ALLOWED_HOLIDAYS:
                raise ValueError(f"invalid holiday={holiday}")
            if functioning not in ALLOWED_FUNCTIONING:
                raise ValueError(f"invalid functioning={functioning}")
                
            timestamp = (record_date, hour)
            if timestamp in timestamps:
                duplicate_count += 1
                raise ValueError("duplicate date-hour")
                
            timestamps.add(timestamp)
            hours_by_date[record_date].add(hour)
            all_dates.append(record_date)
            functioning_counts[functioning] += 1
            valid_rented_values.append(rented)
        except (ValueError, OverflowError) as exc:
            invalid_row_count += 1
            if len(sample_issues) < 30:
                sample_issues.append(f"line {line_number}: {exc}")
                
    if invalid_row_count:
        add_error(errors, f"INVALID_ROWS: {invalid_row_count}")
        
    expected_start = date.fromisoformat(training["train_start"])
    expected_end = date.fromisoformat(training["test_end"])
    expected_days = (expected_end - expected_start).days + 1
    expected_dates = {expected_start + timedelta(days=i) for i in range(expected_days)}
    actual_dates = set(hours_by_date.keys())
    missing_dates = expected_dates - actual_dates
    extra_dates = actual_dates - expected_dates
    
    if missing_dates:
        add_error(errors, f"MISSING_DATES: {len(missing_dates)}")
    if extra_dates:
        add_error(errors, f"UNEXPECTED_DATES: {len(extra_dates)}")
        
    incomplete_dates = [
        d.isoformat()
        for d, hours in hours_by_date.items()
        if hours != set(range(24))
    ]
    if incomplete_dates:
        add_error(errors, f"INCOMPLETE_DATES: {len(incomplete_dates)}")
        
    report = {
        "project": "bikepulse",
        "check_version": "h001",
        "dataset_version": dataset["version"],
        "checked_at_utc": utc_now(),
        "input": {
            "raw_key": raw_key,
            "sha256": actual_sha,
            "file_size_bytes": len(csv_bytes),
            "encoding": encoding
        },
        "metrics": {
            "row_count": len(records),
            "column_count": len(headers),
            "distinct_timestamps": len(timestamps),
            "distinct_dates": len(actual_dates),
            "first_date": min(all_dates).isoformat() if all_dates else None,
            "last_date": max(all_dates).isoformat() if all_dates else None,
            "missing_field_rows": missing_field_rows,
            "invalid_rows": invalid_row_count,
            "duplicate_timestamps": duplicate_count,
            "incomplete_dates": len(incomplete_dates),
            "functioning_counts": dict(functioning_counts),
            "rented_bike_min": min(valid_rented_values) if valid_rented_values else None,
            "rented_bike_max": max(valid_rented_values) if valid_rented_values else None
        },
        "status": "PASSED" if not errors else "FAILED",
        "error_count": len(errors),
        "errors": errors,
        "sample_issues": sample_issues
    }
    return report

def failure_report(raw_key, message):
    return {
        "project": "bikepulse",
        "check_version": "h001",
        "checked_at_utc": utc_now(),
        "input": {
            "raw_key": raw_key
        },
        "status": "FAILED",
        "error_count": 1,
        "errors": [message],
        "sample_issues": []
    }

def run_local(args, contract):
    csv_bytes = Path(args.csv).read_bytes()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    raw_key = manifest["raw_s3_key"]
    try:
        report = validate_dataset(csv_bytes, manifest, contract, raw_key)
    except Exception as exc:
        report = failure_report(raw_key, f"{type(exc).__name__}: {exc}")
        
    output = Path("data/validation/h_local_report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASSED" else 1

def run_s3(contract):
    import boto3
    bucket = os.environ["BIKEPULSE_BUCKET"]
    raw_key = os.environ["RAW_KEY"]
    manifest_key = os.environ["MANIFEST_KEY"]
    run_id = os.environ["VALIDATION_RUN_ID"]
    
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise ValueError("Invalid VALIDATION_RUN_ID")
        
    version = contract["dataset"]["version"]
    report_key = f"curated/_quality_reports/version={version}/run_id={run_id}/quality_report.json"
    
    s3 = boto3.client("s3")
    try:
        raw_bytes = s3.get_object(Bucket=bucket, Key=raw_key)["Body"].read()
        manifest_bytes = s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        report = validate_dataset(raw_bytes, manifest, contract, raw_key)
    except Exception as exc:
        report = failure_report(raw_key, f"{type(exc).__name__}: {exc}")
        
    report["run_id"] = run_id
    report["bucket"] = bucket
    report["report_s3_key"] = report_key
    
    print("VALIDATION_STATUS:", report["status"], flush=True)
    print("QUALITY_REPORT:", json.dumps(report, ensure_ascii=False), flush=True)
    
    # Store report even when data validation fails.
    try:
        s3.put_object(
            Bucket=bucket,
            Key=report_key,
            Body=json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json"
        )
        print(f"QUALITY_REPORT_SAVED: s3://{bucket}/{report_key}", flush=True)
    except Exception as exc:
        print(f"REPORT_UPLOAD_FAILED: {exc}", flush=True)
        return 2
        
    return 0 if report["status"] == "PASSED" else 1

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--csv", default="data/raw/SeoulBikeData.csv")
    parser.add_argument("--manifest", default="data/validation/dv001_manifest.json")
    args = parser.parse_args()
    
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if args.local:
        return run_local(args, contract)
    return run_s3(contract)

if __name__ == "__main__":
    sys.exit(main())
