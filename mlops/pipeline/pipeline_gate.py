import argparse
import hashlib
import io
import json
import os
import sys

import boto3
import numpy as np
import pandas as pd


RAW_KEY = (
    "raw/source=uci-seoul-bike/"
    "version=dv001/SeoulBikeData.csv"
)

CURATED_KEY = (
    "curated/dataset=seoul-bike/"
    "version=dv001/bike_data.parquet"
)

FEATURE_KEY = (
    "features/version=fv001/feature_manifest.json"
)

SPLIT_PREFIX = (
    "splits/version=dv001/feature_version=fv001/"
)

SPLIT_KEY = (
    SPLIT_PREFIX + "split_manifest.json"
)

EXPERIMENT_PREFIX = (
    "models/experiments/feature_version=fv001/"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def download(s3, bucket, key):
    return s3.get_object(
        Bucket=bucket,
        Key=key
    )["Body"].read()


def read_json(s3, bucket, key):
    return json.loads(
        download(s3, bucket, key)
    )


def raw_gate(s3, bucket):
    content = download(
        s3, bucket, RAW_KEY
    )

    dataframe = None

    for encoding in ("cp949", "utf-8-sig"):
        try:
            dataframe = pd.read_csv(
                io.BytesIO(content),
                encoding=encoding
            )
            break
        except UnicodeDecodeError:
            continue

    require(
        dataframe is not None,
        "RAW_ENCODING_UNSUPPORTED"
    )

    require(
        dataframe.shape == (8760, 14),
        "RAW_SHAPE_MISMATCH"
    )

    require(
        {"Date", "Hour", "Rented Bike Count"}
        .issubset(dataframe.columns),
        "RAW_REQUIRED_COLUMNS_MISSING"
    )

    require(
        dataframe["Date"].notna().all(),
        "RAW_DATE_NULL"
    )

    hours = pd.to_numeric(
        dataframe["Hour"],
        errors="coerce"
    )

    demand = pd.to_numeric(
        dataframe["Rented Bike Count"],
        errors="coerce"
    )

    require(
        hours.notna().all()
        and hours.between(0, 23).all(),
        "RAW_HOUR_INVALID"
    )

    require(
        demand.notna().all()
        and (demand >= 0).all(),
        "RAW_TARGET_INVALID"
    )

    print(
        "RAW_GATE: PASSED",
        "ROWS=8760",
        "COLUMNS=14",
        flush=True
    )


def curated_gate(s3, bucket):
    content = download(
        s3, bucket, CURATED_KEY
    )

    df = pd.read_parquet(
        io.BytesIO(content)
    )

    require(
        len(df) == 8760,
        "CURATED_ROWS_MISMATCH"
    )

    require(
        "rented_bike_count" in df.columns,
        "CURATED_TARGET_MISSING"
    )

    require(
        df["rented_bike_count"].notna().all(),
        "CURATED_TARGET_NULL"
    )

    target = pd.to_numeric(
        df["rented_bike_count"],
        errors="coerce"
    )

    require(
        np.isfinite(target.to_numpy()).all()
        and (target >= 0).all(),
        "CURATED_TARGET_INVALID"
    )

    print(
        "CURATED_GATE: PASSED",
        "ROWS=8760",
        flush=True
    )


def feature_gate(s3, bucket):
    report_key = os.environ["FEATURE_REPORT_KEY"]

    report = read_json(
        s3, bucket, report_key
    )

    feature = read_json(
        s3, bucket, FEATURE_KEY
    )

    split = read_json(
        s3, bucket, SPLIT_KEY
    )

    for doc in (report, feature, split):
        require(
            doc.get("status") == "PASSED",
            "FEATURE_UPSTREAM_FAILED"
        )

        require(
            doc.get("dataset_version") == "dv001"
            and doc.get("feature_version") == "fv001",
            "FEATURE_VERSION_MISMATCH"
        )

    cols = feature["model_feature_columns"]

    require(
        len(cols) == 19
        and len(set(cols)) == 19
        and cols == split["model_feature_columns"],
        "FEATURE_COLUMNS_INVALID"
    )

    require(
        report.get("feature_manifest_key")
        == FEATURE_KEY
        and report.get("split_manifest_key")
        == SPLIT_KEY,
        "FEATURE_REPORT_LINEAGE_MISMATCH"
    )

    records = {
        item["key"]: item
        for item in split["artifacts"]
    }

    # Revalidate Train and Validation content.
    for name in (
        "X_train.parquet",
        "y_train.parquet",
        "X_validation.parquet",
        "y_validation.parquet"
    ):
        key = SPLIT_PREFIX + name

        require(
            key in records,
            "SPLIT_ARTIFACT_MISSING: " + name
        )

        content = download(
            s3, bucket, key
        )

        require(
            checksum(content)
            == records[key]["sha256"],
            "SPLIT_SHA256_MISMATCH: " + name
        )

    # Preserve the Test holdout:
    # check existence, but do not load target labels.
    for name in (
        "X_test.parquet",
        "y_test.parquet"
    ):
        key = SPLIT_PREFIX + name

        require(
            key in records,
            "TEST_ARTIFACT_NOT_LISTED"
        )

        s3.head_object(
            Bucket=bucket,
            Key=key
        )

    print(
        "FEATURE_GATE: PASSED",
        "FEATURES=19",
        "TEST_LABELS_NOT_READ=true",
        flush=True
    )


def experiment_gate(s3, bucket, stage):
    if stage == "baseline":
        run_id = os.environ["TRAIN_RUN_ID"]
        worker = "m001"

        expected = [
            "pipelines/dummy.joblib",
            "pipelines/ridge.joblib",
            "pipelines/random_forest.joblib",
            "metrics/comparison.json",
            "training_report.json"
        ]

    else:
        run_id = os.environ["XGB_RUN_ID"]
        worker = "n001"

        expected = [
            "pipelines/xgb_default.joblib",
            "pipelines/xgb_grid.joblib",
            "pipelines/xgb_random.joblib",
            "comparison/combined_comparison.json",
            "training_report.json"
        ]

    prefix = (
        EXPERIMENT_PREFIX
        + f"run_id={run_id}/"
    )

    manifest = read_json(
        s3,
        bucket,
        prefix + "run_manifest.json"
    )

    require(
        manifest.get("status") == "PASSED"
        and manifest.get("run_id") == run_id
        and manifest.get("worker_version") == worker,
        "EXPERIMENT_MANIFEST_FAILED"
    )

    records = {
        item["path"]: item
        for item in manifest["artifacts"]
    }

    for name in expected:
        require(
            name in records,
            "EXPERIMENT_ARTIFACT_MISSING: " + name
        )

        content = download(
            s3, bucket, prefix + name
        )

        require(
            checksum(content)
            == records[name]["sha256"],
            "EXPERIMENT_SHA256_MISMATCH: " + name
        )

    report = read_json(
        s3,
        bucket,
        prefix + "training_report.json"
    )

    require(
        report.get("status") == "PASSED"
        and report.get("feature_count") == 19
        and report.get("test_evaluated") is False,
        "EXPERIMENT_REPORT_FAILED"
    )

    require(
        report.get("feature_report_key")
        == os.environ["FEATURE_REPORT_KEY"],
        "EXPERIMENT_INPUT_MISMATCH"
    )

    if stage == "xgboost":
        require(
            report.get("baseline_run_id")
            == os.environ["TRAIN_RUN_ID"],
            "BASELINE_LINEAGE_MISMATCH"
        )

    print(
        stage.upper() + "_GATE: PASSED",
        flush=True
    )


def selection_gate(s3, bucket):
    selection_id = os.environ["SELECTION_ID"]

    prefix = (
        "models/selections/feature_version=fv001/"
        f"selection_id={selection_id}/"
    )

    content = download(
        s3,
        bucket,
        prefix + "selection.json"
    )

    manifest = read_json(
        s3,
        bucket,
        prefix + "selection_manifest.json"
    )

    require(
        manifest.get("status") == "PASSED"
        and manifest.get("selection_id")
        == selection_id
        and manifest.get("selection_sha256")
        == checksum(content),
        "SELECTION_SHA256_MISMATCH"
    )

    selection = json.loads(content)

    require(
        selection.get("status") == "LOCKED"
        and selection.get("test_evaluated") is False,
        "SELECTION_NOT_LOCKED"
    )

    require(
        selection.get("baseline_run_id")
        == os.environ["TRAIN_RUN_ID"]
        and selection.get("xgboost_run_id")
        == os.environ["XGB_RUN_ID"],
        "SELECTION_LINEAGE_MISMATCH"
    )

    print(
        "SELECTION_GATE: PASSED",
        selection["stage"],
        selection["model_name"],
        flush=True
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--gate",
        required=True,
        choices=[
            "raw",
            "curated",
            "feature",
            "baseline",
            "xgboost",
            "selection",
            "fail"
        ]
    )

    args = parser.parse_args()

    if args.gate == "fail":
        raise ValueError(
            "INTENTIONAL_GATE_FAILURE"
        )

    bucket = os.environ["BIKEPULSE_BUCKET"]
    s3 = boto3.client("s3")

    gates = {
        "raw": raw_gate,
        "curated": curated_gate,
        "feature": feature_gate
    }

    if args.gate in gates:
        gates[args.gate](
            s3, bucket
        )

    elif args.gate in ("baseline", "xgboost"):
        experiment_gate(
            s3,
            bucket,
            args.gate
        )

    elif args.gate == "selection":
        selection_gate(
            s3, bucket
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "PIPELINE_GATE_FAILED:",
            f"{type(exc).__name__}: {exc}",
            flush=True
        )
        sys.exit(1)
