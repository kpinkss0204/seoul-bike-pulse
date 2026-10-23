import argparse
import hashlib
import io
import json
import math
import os
import re
import sys

from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
import numpy as np
import pandas as pd


CONTRACT_PATH = Path(
    "/app/config/feature_split_contract.json"
)

if not CONTRACT_PATH.exists():
    CONTRACT_PATH = Path(
        "config/feature_split_contract.json"
    )


MODEL_FEATURES = [
    "hour",
    "day_of_week",
    "is_weekend",
    "month",
    "day_of_year",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "season",
    "is_holiday",
    "temperature_c",
    "humidity_pct",
    "wind_speed_mps",
    "visibility_10m",
    "dew_point_temperature_c",
    "solar_radiation_mj_m2",
    "rainfall_mm",
    "snowfall_cm"
]

TARGET = "rented_bike_count"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(document):
    return (
        json.dumps(
            document,
            indent=2,
            ensure_ascii=False
        ) + "\n"
    ).encode("utf-8")


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def read_parquet_bytes(content):
    return pd.read_parquet(
        io.BytesIO(content),
        engine="pyarrow"
    )


def to_parquet_bytes(df):
    return df.to_parquet(
        index=False,
        engine="pyarrow",
        compression="snappy"
    )


def verify_source(
    content,
    report,
    expected_key
):
    actual_sha = sha256(content)

    if report.get("status") != "PASSED":
        raise ValueError(
            "TRANSFORMATION_REPORT_NOT_PASSED"
        )

    if report.get("output_key") != expected_key:
        raise ValueError(
            "SOURCE_PARQUET_KEY_MISMATCH"
        )

    output = report.get("output", {})

    if output.get("parquet_sha256") != actual_sha:
        raise ValueError(
            "SOURCE_PARQUET_SHA256_MISMATCH"
        )

    if (
        output.get("row_count") != 8760
        or output.get("column_count") != 14
    ):
        raise ValueError(
            "SOURCE_SCHEMA_METADATA_MISMATCH"
        )

    return actual_sha


def create_feature_table(df):
    required = {
        "date",
        "hour",
        "rented_bike_count",
        "is_functioning_day",
        "season",
        "is_holiday",
        "temperature_c",
        "humidity_pct",
        "wind_speed_mps",
        "visibility_10m",
        "dew_point_temperature_c",
        "solar_radiation_mj_m2",
        "rainfall_mm",
        "snowfall_cm"
    }

    if set(df.columns) != required:
        raise ValueError(
            "CURATED_COLUMNS_MISMATCH"
        )

    if len(df) != 8760:
        raise ValueError(
            "CURATED_ROW_COUNT_MISMATCH"
        )

    df = df.copy()

    df["date"] = pd.to_datetime(
        df["date"],
        errors="raise"
    ).dt.normalize()

    df["hour"] = df["hour"].astype("int8")

    if not df["hour"].between(0, 23).all():
        raise ValueError("INVALID_HOUR")

    df["timestamp"] = (
        df["date"]
        + pd.to_timedelta(
            df["hour"],
            unit="h"
        )
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    if df["timestamp"].duplicated().any():
        raise ValueError(
            "DUPLICATE_TIMESTAMP"
        )

    expected_timestamps = pd.date_range(
        "2017-12-01 00:00:00",
        "2018-11-30 23:00:00",
        freq="h"
    )

    if not df["timestamp"].equals(
        pd.Series(expected_timestamps)
    ):
        raise ValueError(
            "INCOMPLETE_OR_UNEXPECTED_TIMELINE"
        )

    if df.isna().any().any():
        raise ValueError(
            "CURATED_NULL_VALUES"
        )

    df["sample_id"] = (
        df["timestamp"]
        .dt.strftime("%Y-%m-%dT%H:%M:%S")
    )

    df["day_of_week"] = (
        df["date"].dt.dayofweek
    ).astype("int8")

    df["is_weekend"] = (
        df["day_of_week"] >= 5
    )

    df["month"] = (
        df["date"].dt.month
    ).astype("int8")

    df["day_of_year"] = (
        df["date"].dt.dayofyear
    ).astype("int16")

    df["hour_sin"] = np.sin(
        2 * math.pi * df["hour"] / 24
    )

    df["hour_cos"] = np.cos(
        2 * math.pi * df["hour"] / 24
    )

    df["dow_sin"] = np.sin(
        2 * math.pi * df["day_of_week"] / 7
    )

    df["dow_cos"] = np.cos(
        2 * math.pi * df["day_of_week"] / 7
    )

    if not df["season"].isin([
        "winter",
        "spring",
        "summer",
        "autumn"
    ]).all():
        raise ValueError(
            "INVALID_SEASON"
        )

    if not df["is_functioning_day"].isin(
        [True, False]
    ).all():
        raise ValueError(
            "INVALID_FUNCTIONING_FLAG"
        )

    if (df[TARGET] < 0).any():
        raise ValueError(
            "INVALID_TARGET"
        )

    if df["sample_id"].duplicated().any():
        raise ValueError(
            "DUPLICATE_SAMPLE_ID"
        )

    output_columns = (
        [
            "sample_id",
            "date",
            "timestamp"
        ]
        + MODEL_FEATURES
        + [
            TARGET,
            "is_functioning_day"
        ]
    )

    result = df[output_columns].copy()

    if result.isna().any().any():
        raise ValueError(
            "FEATURE_NULL_VALUES"
        )

    return result


def validate_split_dates(policy):
    boundaries = policy["boundaries"]

    parsed = {
        key: pd.Timestamp(value)
        for key, value in boundaries.items()
    }

    if not (
        parsed["train_start"]
        <= parsed["train_end"]
        < parsed["validation_start"]
        <= parsed["validation_end"]
        < parsed["test_start"]
        <= parsed["test_end"]
    ):
        raise ValueError(
            "INVALID_SPLIT_BOUNDARIES"
        )

    if (
        parsed["train_end"] + pd.Timedelta(days=1)
        != parsed["validation_start"]
    ):
        raise ValueError(
            "TRAIN_VALIDATION_GAP"
        )

    if (
        parsed["validation_end"] + pd.Timedelta(days=1)
        != parsed["test_start"]
    ):
        raise ValueError(
            "VALIDATION_TEST_GAP"
        )

    return parsed


def create_splits(feature_table, policy):
    dates = validate_split_dates(policy)

    active = feature_table.loc[
        feature_table["is_functioning_day"]
    ].copy()

    groups = {
        "train": (
            dates["train_start"],
            dates["train_end"]
        ),
        "validation": (
            dates["validation_start"],
            dates["validation_end"]
        ),
        "test": (
            dates["test_start"],
            dates["test_end"]
        )
    }

    splits = {}
    statistics = {}

    all_ids = []

    for split_name, (start, end) in groups.items():
        subset = active.loc[
            active["date"].between(
                start,
                end
            )
        ].copy()

        subset = subset.sort_values(
            "timestamp"
        ).reset_index(drop=True)

        if subset.empty:
            raise ValueError(
                f"EMPTY_SPLIT: {split_name}"
            )

        x_columns = [
            "sample_id",
            "timestamp"
        ] + MODEL_FEATURES

        y_columns = [
            "sample_id",
            TARGET
        ]

        X = subset[x_columns].copy()
        y = subset[y_columns].copy()

        if not X["sample_id"].equals(
            y["sample_id"]
        ):
            raise ValueError(
                f"X_Y_ALIGNMENT_FAILED: {split_name}"
            )

        if X["sample_id"].duplicated().any():
            raise ValueError(
                f"DUPLICATE_ID: {split_name}"
            )

        splits[split_name] = (X, y)

        all_ids.extend(
            X["sample_id"].tolist()
        )

        statistics[split_name] = {
            "rows": int(len(X)),
            "first_timestamp": (
                X["timestamp"].min().isoformat()
            ),
            "last_timestamp": (
                X["timestamp"].max().isoformat()
            ),
            "first_calendar_date": (
                start.date().isoformat()
            ),
            "last_calendar_date": (
                end.date().isoformat()
            ),
            "mean_target": round(
                float(y[TARGET].mean()),
                4
            ),
            "min_target": int(
                y[TARGET].min()
            ),
            "max_target": int(
                y[TARGET].max()
            )
        }

    if len(all_ids) != len(set(all_ids)):
        raise ValueError(
            "OVERLAPPING_SPLITS"
        )

    if set(all_ids) != set(
        active["sample_id"]
    ):
        raise ValueError(
            "LOST_OR_EXTRA_TRAINING_SAMPLES"
        )

    if not (
        splits["train"][0]["timestamp"].max()
        < splits["validation"][0]["timestamp"].min()
        < splits["test"][0]["timestamp"].min()
    ):
        raise ValueError(
            "CHRONOLOGICAL_SPLIT_FAILED"
        )

    return splits, statistics, len(active)


def build_artifacts(
    parquet_bytes,
    transform_report,
    policy,
    curated_key
):
    if (
        policy["dataset_version"] != "dv001"
        or policy["feature_version"] != "fv001"
    ):
        raise ValueError(
            "UNEXPECTED_FEATURE_CONTRACT_VERSION"
        )

    source_sha = verify_source(
        parquet_bytes,
        transform_report,
        curated_key
    )

    raw = read_parquet_bytes(
        parquet_bytes
    )

    feature_table = create_feature_table(raw)

    splits, statistics, active_rows = create_splits(
        feature_table,
        policy
    )

    artifacts = {}

    feature_key = (
        "features/version=fv001/features.parquet"
    )

    artifacts[feature_key] = to_parquet_bytes(
        feature_table
    )

    split_prefix = (
        "splits/version=dv001/"
        "feature_version=fv001/"
    )

    for name, (X, y) in splits.items():
        artifacts[
            split_prefix + f"X_{name}.parquet"
        ] = to_parquet_bytes(X)

        artifacts[
            split_prefix + f"y_{name}.parquet"
        ] = to_parquet_bytes(y)

    artifacts_metadata = [
        {
            "key": key,
            "sha256": sha256(content),
            "size_bytes": len(content)
        }
        for key, content in artifacts.items()
    ]

    feature_manifest = {
        "status": "PASSED",
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "source_parquet_key": curated_key,
        "source_parquet_sha256": source_sha,
        "feature_table_rows": int(
            len(feature_table)
        ),
        "eligible_training_rows": int(
            active_rows
        ),
        "model_feature_columns": MODEL_FEATURES,
        "model_feature_count": len(MODEL_FEATURES),
        "target_column": TARGET,
        "excluded_from_model": [
            "sample_id",
            "date",
            "timestamp",
            "is_functioning_day"
        ],
        "parquet_compression": "snappy",
        "artifacts": [
            item
            for item in artifacts_metadata
            if item["key"].startswith("features/")
        ]
    }

    split_manifest = {
        "status": "PASSED",
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "strategy": policy["strategy"],
        "boundaries": policy["boundaries"],
        "model_feature_columns": MODEL_FEATURES,
        "target_column": TARGET,
        "split_statistics": statistics,
        "total_split_rows": int(
            sum(
                item["rows"]
                for item in statistics.values()
            )
        ),
        "artifacts": [
            item
            for item in artifacts_metadata
            if item["key"].startswith("splits/")
        ]
    }

    feature_manifest_key = (
        "features/version=fv001/"
        "feature_manifest.json"
    )

    split_manifest_key = (
        split_prefix + "split_manifest.json"
    )

    # Data objects are written first.
    # Manifests are written after verification.
    manifests = {
        feature_manifest_key: json_bytes(
            feature_manifest
        ),
        split_manifest_key: json_bytes(
            split_manifest
        )
    }

    result = {
        "status": "PASSED",
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "source_parquet_sha256": source_sha,
        "all_rows": int(len(feature_table)),
        "eligible_training_rows": int(active_rows),
        "model_feature_count": len(MODEL_FEATURES),
        "split_statistics": statistics,
        "feature_manifest_key": feature_manifest_key,
        "split_manifest_key": split_manifest_key
    }

    return artifacts, manifests, result


def local_mode(args, policy):
    parquet_bytes = Path(
        args.parquet
    ).read_bytes()

    report = json.loads(
        Path(args.transform_report).read_text(
            encoding="utf-8"
        )
    )

    artifacts, manifests, result = build_artifacts(
        parquet_bytes,
        report,
        policy,
        args.curated_key
    )

    output_root = Path(
        args.output_dir
    )

    for key, content in {
        **artifacts,
        **manifests
    }.items():
        path = output_root / key

        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        path.write_bytes(content)

        print("LOCAL_ARTIFACT:", path)

    report_path = (
        output_root / "feature_report.json"
    )

    report_path.write_bytes(
        json_bytes(result)
    )

    print(
        "LOCAL_FEATURE_PIPELINE_PASSED",
        flush=True
    )

    return 0


def s3_mode(policy):
    bucket = os.environ["BIKEPULSE_BUCKET"]
    curated_key = os.environ["CURATED_KEY"]
    report_key = os.environ["TRANSFORM_REPORT_KEY"]
    run_id = os.environ["FEATURE_RUN_ID"]

    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
        run_id
    ):
        raise ValueError(
            "INVALID_FEATURE_RUN_ID"
        )

    if curated_key != (
        "curated/dataset=seoul-bike/"
        "version=dv001/bike_data.parquet"
    ):
        raise ValueError(
            "UNEXPECTED_CURATED_KEY"
        )

    if not (
        report_key.startswith(
            "curated/_transformation_reports/"
            "version=dv001/run_id="
        )
        and report_key.endswith(
            "/transformation_report.json"
        )
    ):
        raise ValueError(
            "INVALID_TRANSFORM_REPORT_KEY"
        )

    output_report_key = (
        "curated/_feature_reports/"
        f"version=dv001/run_id={run_id}/"
        "feature_report.json"
    )

    s3 = boto3.client("s3")

    result = {
        "status": "FAILED",
        "run_id": run_id,
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "curated_key": curated_key
    }

    try:
        parquet_object = s3.get_object(
            Bucket=bucket,
            Key=curated_key
        )

        parquet_bytes = (
            parquet_object["Body"].read()
        )

        transform_report = json.loads(
            s3.get_object(
                Bucket=bucket,
                Key=report_key
            )["Body"].read()
        )

        if (
            parquet_object.get(
                "Metadata", {}
            ).get("source-sha256")
            != transform_report.get(
                "source_sha256"
            )
        ):
            raise ValueError(
                "SOURCE_LINEAGE_MISMATCH"
            )

        artifacts, manifests, metrics = build_artifacts(
            parquet_bytes,
            transform_report,
            policy,
            curated_key
        )

        # S3 conditional writes prevent overwrites.
 # No HeadObject preflight is required.
 # This also avoids requiring ListBucket
 # solely to test whether an object is absent.
 for key, content in artifacts.items():
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType="application/octet-stream",
                ChecksumAlgorithm="SHA256",
                IfNoneMatch="*"
            )

            saved = s3.get_object(
                Bucket=bucket,
                Key=key
            )["Body"].read()

            if sha256(saved) != sha256(content):
                raise ValueError(
                    f"OUTPUT_SHA256_MISMATCH: {key}"
                )

            print(
                "FEATURE_ARTIFACT_SAVED:",
                key,
                flush=True
            )

        # Manifests are committed after all
        # data artifacts have been verified.
        for key, content in manifests.items():
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType="application/json",
                IfNoneMatch="*"
            )

            print(
                "FEATURE_MANIFEST_SAVED:",
                key,
                flush=True
            )

        result.update(metrics)
        result["status"] = "PASSED"

    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = (
            f"{type(exc).__name__}: {exc}"
        )

        print(
            "FEATURE_PIPELINE_ERROR:",
            result["error"],
            flush=True
        )

    try:
        s3.put_object(
            Bucket=bucket,
            Key=output_report_key,
            Body=json_bytes(result),
            ContentType="application/json",
            IfNoneMatch="*"
        )

        print(
            "FEATURE_REPORT_SAVED:",
            output_report_key,
            flush=True
        )

    except Exception as exc:
        print(
            "FEATURE_REPORT_UPLOAD_FAILED:",
            exc,
            flush=True
        )

        return 2

    print(
        "FEATURE_PIPELINE_STATUS:",
        result["status"],
        flush=True
    )

    return (
        0 if result["status"] == "PASSED"
        else 1
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--local",
        action="store_true"
    )

    parser.add_argument(
        "--parquet",
        default="data/curated/bike_data.parquet"
    )

    parser.add_argument(
        "--transform-report",
        default="data/validation/i_good_s3_report.json"
    )

    parser.add_argument(
        "--curated-key",
        default=(
            "curated/dataset=seoul-bike/"
            "version=dv001/bike_data.parquet"
        )
    )

    parser.add_argument(
        "--output-dir",
        default="data/features/l_local"
    )

    args = parser.parse_args()

    policy = json.loads(
        CONTRACT_PATH.read_text(
            encoding="utf-8"
        )
    )

    if args.local:
        return local_mode(args, policy)

    return s3_mode(policy)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "FEATURE_PIPELINE_FATAL:",
            f"{type(exc).__name__}: {exc}",
            flush=True
        )
        sys.exit(1)
