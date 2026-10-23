import argparse
import hashlib
import io
import json
import os
import re
import sys

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path.cwd()
CONTRACT_PATH = ROOT / "config/project_contract.json"

RAW_HEADERS = [
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

OUTPUT_COLUMNS = [
    "date",
    "rented_bike_count",
    "hour",
    "temperature_c",
    "humidity_pct",
    "wind_speed_mps",
    "visibility_10m",
    "dew_point_temperature_c",
    "solar_radiation_mj_m2",
    "rainfall_mm",
    "snowfall_cm",
    "season",
    "is_holiday",
    "is_functioning_day"
]

ARROW_SCHEMA = pa.schema([
    pa.field("date", pa.date32()),
    pa.field("rented_bike_count", pa.int32()),
    pa.field("hour", pa.int8()),
    pa.field("temperature_c", pa.float64()),
    pa.field("humidity_pct", pa.int16()),
    pa.field("wind_speed_mps", pa.float64()),
    pa.field("visibility_10m", pa.int32()),
    pa.field("dew_point_temperature_c", pa.float64()),
    pa.field("solar_radiation_mj_m2", pa.float64()),
    pa.field("rainfall_mm", pa.float64()),
    pa.field("snowfall_cm", pa.float64()),
    pa.field("season", pa.string()),
    pa.field("is_holiday", pa.bool_()),
    pa.field("is_functioning_day", pa.bool_())
])


def sha256_hex(content):
    return hashlib.sha256(content).hexdigest()


def normalize_header(value):
    return re.sub(
        r"[^a-z0-9]",
        "",
        value.lower()
    )


def check_quality_gate(
    csv_bytes,
    manifest,
    quality_report,
    contract,
    raw_key
):
    dataset = contract["dataset"]

    actual_sha = sha256_hex(csv_bytes)

    if quality_report.get("status") != "PASSED":
        raise ValueError(
            "QUALITY_GATE_REJECTED: report is not PASSED"
        )

    if quality_report.get("dataset_version") != dataset["version"]:
        raise ValueError(
            "QUALITY_DATASET_VERSION_MISMATCH"
        )

    if quality_report.get("input", {}).get("raw_key") != raw_key:
        raise ValueError(
            "QUALITY_RAW_KEY_MISMATCH"
        )

    if quality_report.get("input", {}).get("sha256") != actual_sha:
        raise ValueError(
            "QUALITY_SHA256_MISMATCH"
        )

    metrics = quality_report.get("metrics", {})

    if metrics.get("row_count") != dataset["expected_rows"]:
        raise ValueError(
            "QUALITY_ROW_COUNT_MISMATCH"
        )

    if metrics.get("column_count") != dataset["expected_columns"]:
        raise ValueError(
            "QUALITY_COLUMN_COUNT_MISMATCH"
        )

    if metrics.get("invalid_rows") != 0:
        raise ValueError(
            "QUALITY_INVALID_ROWS"
        )

    if quality_report.get("error_count") != 0:
        raise ValueError(
            "QUALITY_ERRORS_PRESENT"
        )

    if manifest.get("sha256") != actual_sha:
        raise ValueError(
            "MANIFEST_SHA256_MISMATCH"
        )

    if manifest.get("raw_s3_key") != raw_key:
        raise ValueError(
            "MANIFEST_RAW_KEY_MISMATCH"
        )

    if manifest.get("dataset_version") != dataset["version"]:
        raise ValueError(
            "MANIFEST_DATASET_VERSION_MISMATCH"
        )

    if manifest.get("file_size_bytes") != len(csv_bytes):
        raise ValueError(
            "MANIFEST_FILE_SIZE_MISMATCH"
        )

    if manifest.get("row_count") != dataset["expected_rows"]:
        raise ValueError(
            "MANIFEST_ROW_COUNT_MISMATCH"
        )

    if manifest.get("column_count") != dataset["expected_columns"]:
        raise ValueError(
            "MANIFEST_COLUMN_COUNT_MISMATCH"
        )

    if manifest.get("encoding") not in (
        "utf-8-sig",
        "cp949"
    ):
        raise ValueError(
            "UNSUPPORTED_CSV_ENCODING"
        )

    print(
        "QUALITY_GATE_PASSED",
        flush=True
    )

    return actual_sha


def convert_csv(csv_bytes, manifest, contract):
    dataset = contract["dataset"]

    df = pd.read_csv(
        io.BytesIO(csv_bytes),
        encoding=manifest["encoding"]
    )

    if len(df) != dataset["expected_rows"]:
        raise ValueError("UNEXPECTED_ROW_COUNT")

    if len(df.columns) != len(RAW_HEADERS):
        raise ValueError("UNEXPECTED_COLUMN_COUNT")

    for actual, expected in zip(df.columns, RAW_HEADERS):
        if not normalize_header(actual).startswith(expected):
            raise ValueError(
                f"UNEXPECTED_HEADER: {actual}"
            )

    df.columns = OUTPUT_COLUMNS

    if df.isna().any().any():
        raise ValueError("MISSING_VALUES_DETECTED")

    # Convert date to Python date objects.
    df["date"] = pd.to_datetime(
        df["date"],
        format=dataset["date_format"],
        errors="raise"
    ).dt.date

    # Convert integer columns without silent truncation.
    integer_columns = {
        "rented_bike_count": "int32",
        "hour": "int8",
        "humidity_pct": "int16",
        "visibility_10m": "int32"
    }

    for column, dtype in integer_columns.items():
        numeric = pd.to_numeric(
            df[column],
            errors="raise"
        )

        if (
            not numeric.notna().all()
            or not (numeric % 1 == 0).all()
        ):
            raise ValueError(
                f"INVALID_INTEGER: {column}"
            )

        df[column] = numeric.astype(dtype)

    float_columns = [
        "temperature_c",
        "wind_speed_mps",
        "dew_point_temperature_c",
        "solar_radiation_mj_m2",
        "rainfall_mm",
        "snowfall_cm"
    ]

    for column in float_columns:
        df[column] = pd.to_numeric(
            df[column],
            errors="raise"
        ).astype("float64")

    # Normalize categorical values.
    df["season"] = (
        df["season"]
        .str.strip()
        .str.lower()
    )

    df["is_holiday"] = (
        df["is_holiday"]
        .map({
            "Holiday": True,
            "No Holiday": False
        })
    )

    df["is_functioning_day"] = (
        df["is_functioning_day"]
        .map({
            "Yes": True,
            "No": False
        })
    )

    if df.isna().any().any():
        raise ValueError(
            "INVALID_CATEGORY_OR_MISSING_VALUE"
        )

    if not df["hour"].between(0, 23).all():
        raise ValueError("INVALID_HOUR")

    if (df["rented_bike_count"] < 0).any():
        raise ValueError("INVALID_RENTED_BIKE_COUNT")

    if df.duplicated(
        subset=["date", "hour"]
    ).any():
        raise ValueError(
            "DUPLICATE_DATE_HOUR"
        )

    if not df["season"].isin(
        ["winter", "spring", "summer", "autumn"]
    ).all():
        raise ValueError("INVALID_SEASON")

    # Preserve original observation order.
    table = pa.Table.from_pandas(
        df,
        schema=ARROW_SCHEMA,
        preserve_index=False,
        safe=True
    )

    sink = pa.BufferOutputStream()

    pq.write_table(
        table,
        sink,
        compression="snappy",
        version="1.0"
    )

    parquet_bytes = sink.getvalue().to_pybytes()

    # Round-trip verification before uploading.
    restored = pq.read_table(
        pa.BufferReader(parquet_bytes)
    )

    if not table.equals(
        restored,
        check_metadata=False
    ):
        raise ValueError(
            "PARQUET_ROUNDTRIP_MISMATCH"
        )

    if restored.num_rows != dataset["expected_rows"]:
        raise ValueError(
            "PARQUET_ROW_COUNT_MISMATCH"
        )

    if restored.num_columns != dataset["expected_columns"]:
        raise ValueError(
            "PARQUET_COLUMN_COUNT_MISMATCH"
        )

    statistics = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "distinct_dates": int(
            df["date"].nunique()
        ),
        "first_date": str(df["date"].min()),
        "last_date": str(df["date"].max()),
        "functioning_rows": int(
            df["is_functioning_day"].sum()
        ),
        "non_functioning_rows": int(
            (~df["is_functioning_day"]).sum()
        ),
        "holiday_rows": int(
            df["is_holiday"].sum()
        ),
        "csv_size_bytes": len(csv_bytes),
        "parquet_size_bytes": len(parquet_bytes),
        "parquet_sha256": sha256_hex(
            parquet_bytes
        ),
        "compression": "snappy",
        "schema": [
            {
                "name": field.name,
                "type": str(field.type)
            }
            for field in restored.schema
        ]
    }

    return parquet_bytes, statistics


def process(
    csv_bytes,
    manifest,
    quality_report,
    contract,
    raw_key
):
    source_sha = check_quality_gate(
        csv_bytes,
        manifest,
        quality_report,
        contract,
        raw_key
    )

    parquet_bytes, statistics = convert_csv(
        csv_bytes,
        manifest,
        contract
    )

    return source_sha, parquet_bytes, statistics


def local_mode(args, contract):
    csv_bytes = Path(args.csv).read_bytes()

    manifest = json.loads(
        Path(args.manifest).read_text(
            encoding="utf-8"
        )
    )

    quality_report = json.loads(
        Path(args.quality_report).read_text(
            encoding="utf-8"
        )
    )

    source_sha, parquet_bytes, stats = process(
        csv_bytes,
        manifest,
        quality_report,
        contract,
        manifest["raw_s3_key"]
    )

    output = Path(args.output)
    output.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output.write_bytes(parquet_bytes)

    report = {
        "stage": "data-transformation",
        "status": "PASSED",
        "source_sha256": source_sha,
        "output": stats
    }

    report_path = Path(
        "data/validation/i_local_report.json"
    )

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    report_path.write_text(
        json.dumps(
            report,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print("LOCAL_TRANSFORMATION_PASSED")
    print(json.dumps(report, indent=2))

    return 0


def s3_mode(contract):
    import boto3

    bucket = os.environ["BIKEPULSE_BUCKET"]
    raw_key = os.environ["RAW_KEY"]
    manifest_key = os.environ["MANIFEST_KEY"]
    quality_key = os.environ["QUALITY_REPORT_KEY"]
    output_key = os.environ["CURATED_KEY"]
    run_id = os.environ["TRANSFORM_RUN_ID"]

    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
        run_id
    ):
        raise ValueError(
            "INVALID_TRANSFORM_RUN_ID"
        )

    version = contract["dataset"]["version"]

    if raw_key != (
        f"raw/source=uci-seoul-bike/"
        f"version={version}/SeoulBikeData.csv"
    ):
        raise ValueError("UNEXPECTED_RAW_KEY")

    if output_key != (
        f"curated/dataset=seoul-bike/"
        f"version={version}/bike_data.parquet"
    ):
        raise ValueError("UNEXPECTED_CURATED_KEY")

    if not (
        quality_key.startswith(
            f"curated/_quality_reports/version={version}/"
        )
        and quality_key.endswith("/quality_report.json")
    ):
        raise ValueError("INVALID_QUALITY_REPORT_KEY")

    report_key = (
        f"curated/_transformation_reports/"
        f"version={version}/"
        f"run_id={run_id}/transformation_report.json"
    )

    s3 = boto3.client("s3")

    report = {
        "stage": "data-transformation",
        "worker_version": "i001",
        "run_id": run_id,
        "checked_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "FAILED",
        "input": {
            "raw_key": raw_key,
            "manifest_key": manifest_key,
            "quality_report_key": quality_key
        },
        "output_key": output_key
    }

    try:
        csv_bytes = s3.get_object(
            Bucket=bucket,
            Key=raw_key
        )["Body"].read()

        manifest = json.loads(
            s3.get_object(
                Bucket=bucket,
                Key=manifest_key
            )["Body"].read().decode("utf-8")
        )

        quality_report = json.loads(
            s3.get_object(
                Bucket=bucket,
                Key=quality_key
            )["Body"].read().decode("utf-8")
        )

        source_sha, parquet_bytes, stats = process(
            csv_bytes,
            manifest,
            quality_report,
            contract,
            raw_key
        )

        # Write the curated version only once.
        s3.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=parquet_bytes,
            ContentType="application/octet-stream",
            ChecksumAlgorithm="SHA256",
            IfNoneMatch="*",
            Metadata={
                "dataset-version": version,
                "source-sha256": source_sha,
                "worker-version": "i001"
            }
        )

        # Verify what was actually stored in S3.
        downloaded = s3.get_object(
            Bucket=bucket,
            Key=output_key
        )["Body"].read()

        if sha256_hex(downloaded) != stats["parquet_sha256"]:
            raise ValueError(
                "S3_PARQUET_SHA256_MISMATCH"
            )

        stored_table = pq.read_table(
            pa.BufferReader(downloaded)
        )

        if (
            stored_table.num_rows != stats["row_count"]
            or stored_table.num_columns != stats["column_count"]
        ):
            raise ValueError(
                "S3_PARQUET_SCHEMA_OR_ROW_MISMATCH"
            )

        report["status"] = "PASSED"
        report["source_sha256"] = source_sha
        report["output"] = stats

        print(
            f"PARQUET_SAVED: s3://{bucket}/{output_key}",
            flush=True
        )

    except Exception as exc:
        report["status"] = "FAILED"
        report["error"] = (
            f"{type(exc).__name__}: {exc}"
        )

        print(
            "TRANSFORMATION_ERROR:",
            report["error"],
            flush=True
        )

    try:
        s3.put_object(
            Bucket=bucket,
            Key=report_key,
            Body=json.dumps(
                report,
                indent=2
            ).encode("utf-8"),
            ContentType="application/json",
            IfNoneMatch="*"
        )

        print(
            f"TRANSFORMATION_REPORT_SAVED: "
            f"s3://{bucket}/{report_key}",
            flush=True
        )

    except Exception as exc:
        print(
            "REPORT_UPLOAD_FAILED:",
            exc,
            flush=True
        )
        return 2

    print(
        "TRANSFORMATION_STATUS:",
        report["status"],
        flush=True
    )

    return 0 if report["status"] == "PASSED" else 1


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--local", action="store_true")

    parser.add_argument(
        "--csv",
        default="data/raw/SeoulBikeData.csv"
    )

    parser.add_argument(
        "--manifest",
        default="data/validation/dv001_manifest.json"
    )

    parser.add_argument(
        "--quality-report",
        default="data/validation/h_passed_quality_report.json"
    )

    parser.add_argument(
        "--output",
        default="data/curated/bike_data.parquet"
    )

    args = parser.parse_args()

    contract = json.loads(
        CONTRACT_PATH.read_text(
            encoding="utf-8"
        )
    )

    if args.local:
        return local_mode(args, contract)

    return s3_mode(contract)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "TRANSFORMATION_FATAL_ERROR:",
            f"{type(exc).__name__}: {exc}",
            flush=True
        )
        sys.exit(1)
