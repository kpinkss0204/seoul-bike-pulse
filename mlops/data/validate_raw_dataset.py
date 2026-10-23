import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "data/raw/SeoulBikeData.csv"
CONTRACT_PATH = ROOT / "config/project_contract.json"
REPORT_PATH = ROOT / "data/validation/dv001_report.json"

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

def normalize_header(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())

def calculate_sha256(path):
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def read_csv_with_encoding(path):
    for encoding in ["utf-8-sig", "cp949"]:
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                rows = list(csv.reader(f, strict=True))
            return rows, encoding
        except UnicodeError:
            continue
    raise RuntimeError("CSV decoding failed: UTF-8 and CP949.")

def main():
    with CONTRACT_PATH.open("r", encoding="utf-8") as f:
        contract = json.load(f)
    
    dataset = contract["dataset"]
    if not CSV_PATH.exists():
        raise FileNotFoundError(CSV_PATH)
        
    rows, encoding = read_csv_with_encoding(CSV_PATH)
    if not rows:
        raise ValueError("CSV is empty.")
        
    headers = rows[0]
    records = rows[1:]
    errors = []
    
    expected_columns = dataset["expected_columns"]
    expected_rows = dataset["expected_rows"]
    
    if len(headers) != expected_columns:
        errors.append(f"Expected {expected_columns} columns, got {len(headers)}.")
    if len(records) != expected_rows:
        errors.append(f"Expected {expected_rows} rows, got {len(records)}.")
        
    if len(headers) == len(EXPECTED_HEADERS):
        for index, (actual, expected) in enumerate(zip(headers, EXPECTED_HEADERS), start=1):
            normalized = normalize_header(actual)
            if not normalized.startswith(expected):
                errors.append(f"Column {index}: unexpected header '{actual}'.")
                
    timestamps = set()
    dates = []
    hours_by_date = defaultdict(set)
    rented_counts = []
    functioning_counts = Counter()
    missing_rows = 0
    
    for line_number, row in enumerate(records, start=2):
        try:
            if len(row) != expected_columns:
                raise ValueError(f"Column count {len(row)}")
            if any(value.strip() == "" for value in row):
                missing_rows += 1
                raise ValueError("Empty field detected")
                
            date_value = datetime.strptime(row[0].strip(), dataset["date_format"]).date()
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
                raise ValueError("Negative bike count")
            if not 0 <= hour <= 23:
                raise ValueError("Hour outside 0-23")
            if not 0 <= humidity <= 100:
                raise ValueError("Humidity outside 0-100")
            if any(value < 0 for value in [wind, visibility, solar, rainfall, snowfall]):
                raise ValueError("Negative nonnegative feature")
                
            # Validate numeric weather fields.
            for value in [temperature, wind, visibility, dew_point, solar, rainfall, snowfall]:
                if not (float("-inf") < value < float("inf")):
                    raise ValueError("Non-finite numeric value")
                    
            if season not in {"Winter", "Spring", "Summer", "Autumn"}:
                raise ValueError(f"Invalid season: {season}")
            if holiday not in {"Holiday", "No Holiday"}:
                raise ValueError(f"Invalid holiday: {holiday}")
            if functioning not in {"Yes", "No"}:
                raise ValueError(f"Invalid functioning day: {functioning}")
                
            timestamp_key = (date_value.isoformat(), hour)
            if timestamp_key in timestamps:
                raise ValueError("Duplicate date-hour")
                
            timestamps.add(timestamp_key)
            dates.append(date_value)
            hours_by_date[date_value].add(hour)
            rented_counts.append(rented)
            functioning_counts[functioning] += 1
        except (ValueError, OverflowError) as exc:
            errors.append(f"Line {line_number}: {exc}")
            
    min_date = min(dates) if dates else None
    max_date = max(dates) if dates else None
    
    if min_date and min_date.isoformat() != "2017-12-01":
        errors.append("Unexpected first date")
    if max_date and max_date.isoformat() != "2018-11-30":
        errors.append("Unexpected last date")
    if len(hours_by_date) != 365:
        errors.append("Expected 365 distinct dates")
        
    incomplete_dates = [
        date_value.isoformat()
        for date_value, hours in hours_by_date.items()
        if hours != set(range(24))
    ]
    if incomplete_dates:
        errors.append("Some dates do not contain all 24 hours")
        
    report = {
        "project": "bikepulse",
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["version"],
        "filename": CSV_PATH.name,
        "encoding": encoding,
        "file_size_bytes": CSV_PATH.stat().st_size,
        "sha256": calculate_sha256(CSV_PATH),
        "row_count": len(records),
        "column_count": len(headers),
        "headers": headers,
        "date_start": min_date.isoformat() if min_date else None,
        "date_end": max_date.isoformat() if max_date else None,
        "distinct_dates": len(hours_by_date),
        "distinct_timestamps": len(timestamps),
        "missing_field_rows": missing_rows,
        "functioning_counts": dict(functioning_counts),
        "min_rented_bikes": min(rented_counts) if rented_counts else None,
        "max_rented_bikes": max(rented_counts) if rented_counts else None,
        "validation_status": "PASSED" if not errors else "FAILED",
        "errors": errors[:20]
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    
    print("=" * 55)
    print("BIKEPULSE RAW DATASET VALIDATION")
    print("=" * 55)
    print("Filename :", CSV_PATH.name)
    print("Encoding :", encoding)
    print("Rows :", len(records))
    print("Columns :", len(headers))
    print("First Date :", report["date_start"])
    print("Last Date :", report["date_end"])
    print("Unique Hours :", len(timestamps))
    print("Distinct Days :", len(hours_by_date))
    print("Missing Rows :", missing_rows)
    print("Functioning :", dict(functioning_counts))
    print("=" * 55)
    print("RESULT:", report["validation_status"])
    print("=" * 55)
    
    if errors:
        for error in errors[:20]:
            print("ERROR:", error)
        sys.exit(1)

if __name__ == "__main__":
    main()
