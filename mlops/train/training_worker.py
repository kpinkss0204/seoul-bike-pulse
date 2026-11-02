import argparse
import hashlib
import io
import json
import math
import os
import platform
import re
import sys
import time

from datetime import datetime, timezone
from pathlib import Path

import boto3
import joblib
import numpy as np
import pandas as pd
import sklearn

from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import (
    OneHotEncoder,
    StandardScaler
)


DATASET_VERSION = "dv001"
FEATURE_VERSION = "fv001"
WORKER_VERSION = "m001"

FEATURE_MANIFEST_KEY = (
    "features/version=fv001/feature_manifest.json"
)

SPLIT_PREFIX = (
    "splits/version=dv001/feature_version=fv001/"
)

SPLIT_MANIFEST_KEY = (
    SPLIT_PREFIX + "split_manifest.json"
)

CATEGORICAL_FEATURES = [
    "season",
    "is_weekend",
    "is_holiday"
]

TARGET = "rented_bike_count"


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(document):
    return (
        json.dumps(
            document,
            indent=2,
            ensure_ascii=False,
            allow_nan=False
        ) + "\n"
    ).encode("utf-8")


def load_json(reader, key):
    return json.loads(
        reader(key).decode("utf-8")
    )


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_verified_split(
    reader,
    split_manifest,
    split_name,
    feature_columns
):
    artifact_records = {
        item["key"]: item
        for item in split_manifest["artifacts"]
    }

    frames = {}

    for prefix in ("X", "y"):
        key = (
            SPLIT_PREFIX
            + f"{prefix}_{split_name}.parquet"
        )

        require(
            key in artifact_records,
            f"MISSING_ARTIFACT_METADATA: {key}"
        )

        content = reader(key)

        require(
            sha256(content)
            == artifact_records[key]["sha256"],
            f"SPLIT_SHA256_MISMATCH: {key}"
        )

        frames[prefix] = pd.read_parquet(
            io.BytesIO(content)
        )

    X = frames["X"]
    y = frames["y"]

    require(
        list(X.columns)
        == ["sample_id", "timestamp"] + feature_columns,
        f"X_COLUMNS_MISMATCH: {split_name}"
    )

    require(
        list(y.columns)
        == ["sample_id", TARGET],
        f"Y_COLUMNS_MISMATCH: {split_name}"
    )

    require(
        len(X) > 0 and len(X) == len(y),
        f"X_Y_ROW_COUNT_MISMATCH: {split_name}"
    )

    require(
        len(X)
        == split_manifest["split_statistics"][split_name]["rows"],
        f"MANIFEST_ROW_COUNT_MISMATCH: {split_name}"
    )

    require(
        X["sample_id"].equals(y["sample_id"]),
        f"SAMPLE_ID_ALIGNMENT_FAILED: {split_name}"
    )

    require(
        not X["sample_id"].duplicated().any(),
        f"DUPLICATE_SAMPLE_ID: {split_name}"
    )

    timestamps = pd.to_datetime(
        X["timestamp"],
        errors="raise"
    )

    require(
        timestamps.is_monotonic_increasing,
        f"NOT_CHRONOLOGICAL: {split_name}"
    )

    require(
        X[feature_columns].notna().all().all(),
        f"FEATURE_MISSING_VALUES: {split_name}"
    )

    numeric_columns = [
        column
        for column in feature_columns
        if column not in CATEGORICAL_FEATURES
    ]

    numeric = X[numeric_columns].to_numpy(
        dtype="float64"
    )

    require(
        np.isfinite(numeric).all(),
        f"NON_FINITE_FEATURE: {split_name}"
    )

    target = pd.to_numeric(
        y[TARGET],
        errors="raise"
    )

    require(
        np.isfinite(target.to_numpy()).all(),
        f"NON_FINITE_TARGET: {split_name}"
    )

    require(
        (target >= 0).all(),
        f"NEGATIVE_TARGET: {split_name}"
    )

    X_model = X[feature_columns].copy()

    return X_model, target, X, timestamps


def load_training_data(reader, feature_report_key):
    feature_report = load_json(
        reader,
        feature_report_key
    )

    feature_manifest = load_json(
        reader,
        FEATURE_MANIFEST_KEY
    )

    split_manifest = load_json(
        reader,
        SPLIT_MANIFEST_KEY
    )

    for name, document in [
        ("feature_report", feature_report),
        ("feature_manifest", feature_manifest),
        ("split_manifest", split_manifest)
    ]:
        require(
            document.get("status") == "PASSED",
            f"QUALITY_GATE_REJECTED: {name}"
        )

        require(
            document.get("dataset_version")
            == DATASET_VERSION,
            f"DATASET_VERSION_MISMATCH: {name}"
        )

        require(
            document.get("feature_version")
            == FEATURE_VERSION,
            f"FEATURE_VERSION_MISMATCH: {name}"
        )

    require(
        feature_report.get("feature_manifest_key")
        == FEATURE_MANIFEST_KEY,
        "FEATURE_REPORT_MANIFEST_KEY_MISMATCH"
    )

    require(
        feature_report.get("split_manifest_key")
        == SPLIT_MANIFEST_KEY,
        "FEATURE_REPORT_SPLIT_KEY_MISMATCH"
    )

    require(
        feature_report.get("all_rows") == 8760,
        "FEATURE_REPORT_TOTAL_ROWS_MISMATCH"
    )

    require(
        feature_manifest.get("feature_table_rows") == 8760,
        "FEATURE_MANIFEST_TOTAL_ROWS_MISMATCH"
    )

    require(
        feature_report.get("source_parquet_sha256")
        == feature_manifest.get("source_parquet_sha256"),
        "SOURCE_LINEAGE_MISMATCH"
    )

    feature_columns = (
        feature_manifest["model_feature_columns"]
    )

    require(
        feature_columns
        == split_manifest["model_feature_columns"],
        "FEATURE_COLUMN_LIST_MISMATCH"
    )

    require(
        len(feature_columns) == 19
        and len(set(feature_columns)) == 19,
        "EXPECTED_19_UNIQUE_FEATURES"
    )

    require(
        set(CATEGORICAL_FEATURES).issubset(
            set(feature_columns)
        ),
        "REQUIRED_CATEGORICAL_COLUMNS_MISSING"
    )

    require(
        feature_manifest.get("target_column") == TARGET
        and split_manifest.get("target_column") == TARGET,
        "TARGET_COLUMN_MISMATCH"
    )

    require(
        TARGET not in feature_columns
        and "sample_id" not in feature_columns
        and "timestamp" not in feature_columns
        and "is_functioning_day" not in feature_columns,
        "TARGET_OR_METADATA_LEAKAGE"
    )

    X_train, y_train, X_train_full, t_train = (
        read_verified_split(
            reader,
            split_manifest,
            "train",
            feature_columns
        )
    )

    X_val, y_val, X_val_full, t_val = (
        read_verified_split(
            reader,
            split_manifest,
            "validation",
            feature_columns
        )
    )

    require(
        t_train.max() < t_val.min(),
        "TRAIN_VALIDATION_CHRONOLOGY_FAILED"
    )

    require(
        set(X_train_full["sample_id"]).isdisjoint(
            set(X_val_full["sample_id"])
        ),
        "TRAIN_VALIDATION_OVERLAP"
    )

    print(
        "TRAINING_INPUT_VALIDATION: PASSED",
        flush=True
    )

    print(
        f"TRAIN_ROWS: {len(X_train)}",
        flush=True
    )

    print(
        f"VALIDATION_ROWS: {len(X_val)}",
        flush=True
    )

    return {
        "X_train": X_train,
        "y_train": y_train,
        "X_validation": X_val,
        "y_validation": y_val,
        "validation_metadata": X_val_full[
            ["sample_id", "timestamp"]
        ].copy(),
        "feature_columns": feature_columns,
        "feature_report_key": feature_report_key,
        "feature_manifest": feature_manifest,
        "split_manifest": split_manifest
    }


def build_preprocessor(feature_columns):
    numeric_columns = [
        column
        for column in feature_columns
        if column not in CATEGORICAL_FEATURES
    ]

    numeric_pipeline = Pipeline([
        (
            "imputer",
            SimpleImputer(strategy="median")
        ),
        (
            "scaler",
            StandardScaler()
        )
    ])

    categorical_pipeline = Pipeline([
        (
            "imputer",
            SimpleImputer(strategy="most_frequent")
        ),
        (
            "onehot",
            OneHotEncoder(
                handle_unknown="ignore",
                sparse_output=False
            )
        )
    ])

    return ColumnTransformer(
        transformers=[
            (
                "numeric",
                numeric_pipeline,
                numeric_columns
            ),
            (
                "categorical",
                categorical_pipeline,
                CATEGORICAL_FEATURES
            )
        ],
        remainder="drop",
        verbose_feature_names_out=True
    )


def make_pipeline(model, feature_columns):
    return Pipeline([
        (
            "preprocess",
            build_preprocessor(feature_columns)
        ),
        (
            "model",
            model
        )
    ])


def calculate_metrics(actual, prediction):
    prediction = np.asarray(
        prediction,
        dtype="float64"
    )

    require(
        np.isfinite(prediction).all(),
        "NON_FINITE_PREDICTIONS"
    )

    return {
        "mae": round(
            float(
                mean_absolute_error(
                    actual, prediction
                )
            ),
            6
        ),
        "rmse": round(
            float(
                math.sqrt(
                    mean_squared_error(
                        actual, prediction
                    )
                )
            ),
            6
        ),
        "r2": round(
            float(
                r2_score(
                    actual, prediction
                )
            ),
            6
        ),
        "negative_predictions": int(
            (prediction < 0).sum()
        )
    }


def build_experiment(reader, feature_report_key, run_id):
    data = load_training_data(
        reader,
        feature_report_key
    )

    X_train = data["X_train"]
    y_train = data["y_train"]

    X_val = data["X_validation"]
    y_val = data["y_validation"]

    feature_columns = data["feature_columns"]

    models = {
        "dummy": DummyRegressor(
            strategy="mean"
        ),
        "ridge": Ridge(
            alpha=10.0
        ),
        "random_forest": RandomForestRegressor(
            n_estimators=160,
            max_depth=18,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=1
        )
    }

    artifacts = {}
    comparison = []

    validation_predictions = (
        data["validation_metadata"].copy()
    )

    validation_predictions["actual_rentals"] = (
        y_val.to_numpy()
    )

    for model_name, estimator in models.items():
        pipeline = make_pipeline(
            estimator,
            feature_columns
        )

        started = time.perf_counter()

        pipeline.fit(
            X_train,
            y_train
        )

        fit_seconds = round(
            time.perf_counter() - started,
            4
        )

        train_pred = pipeline.predict(
            X_train
        )

        val_pred = pipeline.predict(
            X_val
        )

        train_metrics = calculate_metrics(
            y_train,
            train_pred
        )

        val_metrics = calculate_metrics(
            y_val,
            val_pred
        )

        model_report = {
            "model_name": model_name,
            "worker_version": WORKER_VERSION,
            "feature_version": FEATURE_VERSION,
            "train_rows": len(X_train),
            "validation_rows": len(X_val),
            "fit_seconds": fit_seconds,
            "train": train_metrics,
            "validation": val_metrics,
            "prediction_policy": (
                "Raw continuous predictions; "
                "no clipping or rounding"
            )
        }

        model_buffer = io.BytesIO()

        joblib.dump(
            pipeline,
            model_buffer,
            compress=3
        )

        artifacts[
            f"pipelines/{model_name}.joblib"
        ] = model_buffer.getvalue()

        artifacts[
            f"metrics/{model_name}.json"
        ] = json_bytes(model_report)

        validation_predictions[
            f"prediction_{model_name}"
        ] = val_pred

        comparison.append({
            "model": model_name,
            "train_mae": train_metrics["mae"],
            "validation_mae": val_metrics["mae"],
            "validation_rmse": val_metrics["rmse"],
            "validation_r2": val_metrics["r2"],
            "fit_seconds": fit_seconds
        })

        print(
            f"MODEL_FINISHED: {model_name} "
            f"TRAIN_MAE={train_metrics['mae']} "
            f"VALIDATION_MAE={val_metrics['mae']}",
            flush=True
        )

    comparison.sort(
        key=lambda item: (
            item["validation_mae"],
            item["validation_rmse"]
        )
    )

    winner = comparison[0]["model"]

    artifacts[
        "metrics/comparison.json"
    ] = json_bytes({
        "selection_metric": "validation_mae",
        "tie_break_metric": "validation_rmse",
        "selected_baseline": winner,
        "models": comparison,
        "test_evaluated": False
    })

    prediction_buffer = io.BytesIO()

    validation_predictions.to_parquet(
        prediction_buffer,
        engine="pyarrow",
        compression="snappy",
        index=False
    )

    artifacts[
        "predictions/validation_predictions.parquet"
    ] = prediction_buffer.getvalue()

    training_report = {
        "status": "PASSED",
        "project": "bikepulse",
        "worker_version": WORKER_VERSION,
        "run_id": run_id,
        "created_at_utc": utc_now(),
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "feature_report_key": feature_report_key,
        "train_rows": len(X_train),
        "validation_rows": len(X_val),
        "feature_columns": feature_columns,
        "feature_count": len(feature_columns),
        "selection_metric": "validation_mae",
        "selected_baseline": winner,
        "comparison": comparison,
        "test_evaluated": False,
        "final_model_selection_completed": False,
        "python_version": platform.python_version(),
        "sklearn_version": sklearn.__version__,
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
        "joblib_version": joblib.__version__
    }

    artifacts[
        "training_report.json"
    ] = json_bytes(training_report)

    manifest = {
        "status": "PASSED",
        "worker_version": WORKER_VERSION,
        "run_id": run_id,
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "created_at_utc": utc_now(),
        "test_evaluated": False,
        "artifacts": [
            {
                "path": path,
                "sha256": sha256(content),
                "size_bytes": len(content)
            }
            for path, content in artifacts.items()
        ]
    }

    # Completion marker: uploaded last in S3 mode.
    artifacts[
        "run_manifest.json"
    ] = json_bytes(manifest)

    return artifacts


def local_mode(args):
    root = Path(args.input_dir)

    def reader(key):
        return (root / key).read_bytes()

    artifacts = build_experiment(
        reader,
        args.feature_report_key,
        args.run_id
    )

    output = Path(args.output_dir)

    for name, content in artifacts.items():
        destination = output / name

        destination.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        destination.write_bytes(content)

        print(
            "LOCAL_MODEL_ARTIFACT:",
            destination
        )

    print(
        "LOCAL_TRAINING_STATUS: PASSED",
        flush=True
    )

    return 0


def s3_mode():
    bucket = os.environ["BIKEPULSE_BUCKET"]
    feature_report_key = os.environ["FEATURE_REPORT_KEY"]
    run_id = os.environ["TRAIN_RUN_ID"]

    require(
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
            run_id
        ),
        "INVALID_TRAIN_RUN_ID"
    )

    prefix = (
        "models/experiments/"
        "feature_version=fv001/"
        f"run_id={run_id}/"
    )

    s3 = boto3.client("s3")

    def reader(key):
        return s3.get_object(
            Bucket=bucket,
            Key=key
        )["Body"].read()

    try:
        artifacts = build_experiment(
            reader,
            feature_report_key,
            run_id
        )

        # Upload the completion manifest last.
        for name, content in artifacts.items():
            key = prefix + name

            if name.endswith(".json"):
                content_type = "application/json"
            elif name.endswith(".parquet"):
                content_type = "application/octet-stream"
            else:
                content_type = "application/octet-stream"

            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                IfNoneMatch="*"
            )

            downloaded = reader(key)

            require(
                sha256(downloaded) == sha256(content),
                f"UPLOADED_OBJECT_SHA256_MISMATCH: {name}"
            )

            print(
                "MODEL_ARTIFACT_SAVED:",
                key,
                flush=True
            )

        print(
            "TRAINING_STATUS: PASSED",
            flush=True
        )

        print(
            "TRAINING_MANIFEST_SAVED:",
            prefix + "run_manifest.json",
            flush=True
        )

        return 0

    except Exception as exc:
        message = (
            f"{type(exc).__name__}: {exc}"
        )

        print(
            "TRAINING_ERROR:",
            message,
            flush=True
        )

        failure = {
            "status": "FAILED",
            "run_id": run_id,
            "worker_version": WORKER_VERSION,
            "created_at_utc": utc_now(),
            "error": message
        }

        try:
            s3.put_object(
                Bucket=bucket,
                Key=prefix + "failure_report.json",
                Body=json_bytes(failure),
                ContentType="application/json",
                IfNoneMatch="*"
            )

            print(
                "TRAINING_FAILURE_REPORT_SAVED",
                flush=True
            )

        except Exception as upload_error:
            print(
                "FAILURE_REPORT_UPLOAD_ERROR:",
                upload_error,
                flush=True
            )

        return 1


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--local",
        action="store_true"
    )

    parser.add_argument(
        "--input-dir",
        default="data/training/input"
    )

    parser.add_argument(
        "--output-dir",
        default="data/training/output"
    )

    parser.add_argument(
        "--feature-report-key",
        default=None
    )

    parser.add_argument(
        "--run-id",
        default="m-local-001"
    )

    args = parser.parse_args()

    if args.local:
        require(
            args.feature_report_key is not None,
            "LOCAL_FEATURE_REPORT_KEY_REQUIRED"
        )

        return local_mode(args)

    return s3_mode()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(
            "TRAINING_FATAL_ERROR:",
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        sys.exit(1)
