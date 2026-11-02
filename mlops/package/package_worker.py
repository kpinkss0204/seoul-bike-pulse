import argparse
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys

from importlib.metadata import version as pkg_version
from pathlib import Path

import boto3
import joblib
import numpy as np
import pandas as pd
import sklearn

from sklearn.pipeline import Pipeline


MODEL_ID = "bike-demand"

FEATURE_KEY = (
    "features/version=fv001/feature_manifest.json"
)

SPLIT_KEY = (
    "splits/version=dv001/"
    "feature_version=fv001/split_manifest.json"
)

SPLIT_PREFIX = (
    "splits/version=dv001/feature_version=fv001/"
)

MODEL_ROOT = (
    "models/experiments/feature_version=fv001/"
)

SELECT_ROOT = (
    "models/selections/feature_version=fv001/"
)

EVAL_ROOT = (
    "models/evaluations/feature_version=fv001/"
)

TARGET = "rented_bike_count"

MODELS = {
    "M": {"dummy", "ridge", "random_forest"},
    "N": {"xgb_default", "xgb_grid", "xgb_random"}
}

BOOL_COLS = {
    "is_weekend",
    "is_holiday"
}

INT_COLS = {
    "hour",
    "day_of_week",
    "month",
    "day_of_year"
}

WEATHER_EXTRA = [
    "visibility_10m",
    "dew_point_temperature_c",
    "solar_radiation_mj_m2"
]


def demand(condition, message):
    if not condition:
        raise ValueError(message)


def safe_id(value, name):
    demand(
        bool(
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
                value or ""
            )
        ),
        "INVALID_" + name
    )
    return value


def sha256(content):
    return hashlib.sha256(
        content
    ).hexdigest()


def json_bytes(obj):
    return (
        json.dumps(
            obj,
            indent=2,
            ensure_ascii=False,
            allow_nan=False
        ) + "\n"
    ).encode("utf-8")


def json_read(read, key):
    return json.loads(
        read(key)
    )


def indexed_artifact(read, root, manifest, name):
    lookup = {
        item["path"]: item
        for item in manifest["artifacts"]
    }

    demand(
        name in lookup,
        "ARTIFACT_NOT_LISTED: " + name
    )

    content = read(
        root + name
    )

    demand(
        sha256(content)
        == lookup[name]["sha256"],
        "ARTIFACT_SHA256_MISMATCH: " + name
    )

    demand(
        len(content)
        == lookup[name]["size_bytes"],
        "ARTIFACT_SIZE_MISMATCH: " + name
    )

    return content


def verify_selection(read, selection_id):
    root = (
        SELECT_ROOT
        + f"selection_id={safe_id(selection_id, 'SELECTION_ID')}/"
    )

    content = read(
        root + "selection.json"
    )

    manifest = json_read(
        read,
        root + "selection_manifest.json"
    )

    demand(
        manifest.get("status") == "PASSED"
        and manifest.get("selection_id") == selection_id
        and manifest.get("selection_sha256")
        == sha256(content),
        "SELECTION_INTEGRITY_FAILED"
    )

    data = json.loads(content)

    demand(
        data.get("status") == "LOCKED"
        and data.get("selection_id") == selection_id
        and data.get("test_evaluated") is False,
        "NOT_LOCKED"
    )

    stage = data.get("stage")
    name = data.get("model_name")

    demand(
        stage in MODELS
        and name in MODELS[stage],
        "MODEL_NOT_ALLOWED"
    )

    safe_id(
        data.get("source_run_id"),
        "SOURCE_RUN_ID"
    )

    expected_key = (
        MODEL_ROOT
        + f"run_id={data['source_run_id']}/"
        + f"pipelines/{name}.joblib"
    )

    demand(
        data.get("source_model_key") == expected_key,
        "SOURCE_MODEL_KEY_MISMATCH"
    )

    cols = data.get("feature_columns", [])

    demand(
        len(cols) == 19
        and len(set(cols)) == 19,
        "FEATURE_SCHEMA_BAD"
    )

    return data


def version_match(report):
    expected = {
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "joblib_version": joblib.__version__
    }

    for key, actual in expected.items():
        demand(
            str(report.get(key)) == str(actual),
            f"RUNTIME_VERSION_MISMATCH: {key}: "
            f"training={report.get(key)} "
            f"current={actual}"
        )

    training_python = ".".join(
        str(report["python_version"]).split(".")[:2]
    )

    current_python = ".".join(
        platform.python_version().split(".")[:2]
    )

    demand(
        training_python == current_python,
        "PYTHON_VERSION_MISMATCH"
    )

    if report["worker_version"] == "n001":
        import xgboost

        demand(
            report.get("xgboost_version")
            == xgboost.__version__,
            "XGBOOST_VERSION_MISMATCH"
        )

    return expected


def infer_schema(cols):
    properties = {}

    for col in cols:
        if col == "season":
            properties[col] = {
                "type": "string",
                "enum": [
                    "winter",
                    "spring",
                    "summer",
                    "autumn"
                ]
            }

        elif col in BOOL_COLS:
            properties[col] = {
                "type": "boolean"
            }

        elif col in INT_COLS:
            limits = {
                "hour": (0, 23),
                "day_of_week": (0, 6),
                "month": (1, 12),
                "day_of_year": (1, 366)
            }

            low, high = limits[col]

            properties[col] = {
                "type": "integer",
                "minimum": low,
                "maximum": high
            }

        else:
            properties[col] = {
                "type": "number"
            }

            if col == "humidity_pct":
                properties[col].update({
                    "minimum": 0,
                    "maximum": 100
                })

            elif col in [
                "rainfall_mm",
                "snowfall_cm",
                "wind_speed_mps",
                "solar_radiation_mj_m2",
                "visibility_10m"
            ]:
                properties[col]["minimum"] = 0

    return {
        "schema_version": "fv001",
        "input_mode": "feature-complete-19",
        "type": "object",
        "required": cols,
        "additionalProperties": False,
        "properties": properties,
        "output": {
            "type": "number",
            "unit": "rented_bikes_per_hour",
            "postprocessing": "none"
        },
        "frontend_8_field_contract_supported": False,
        "missing_from_original_8_fields": WEATHER_EXTRA
    }


def make_example(X, cols):
    sample = X.iloc[0]

    payload = {}

    for col in cols:
        value = sample[col]

        demand(
            not pd.isna(value),
            "NULL_SMOKE_FEATURE: " + col
        )

        if col in BOOL_COLS:
            payload[col] = bool(value)

        elif col in INT_COLS:
            payload[col] = int(value)

        elif col == "season":
            payload[col] = str(value)

        else:
            number = float(value)

            demand(
                np.isfinite(number),
                "NON_FINITE_SMOKE_FEATURE: " + col
            )

            payload[col] = number

    return str(sample["sample_id"]), payload


def build(read, selection_id, eval_run, model_version):
    choice = verify_selection(
        read,
        selection_id
    )

    safe_id(
        eval_run,
        "EVALUATION_RUN_ID"
    )

    demand(
        bool(
            re.fullmatch(
                r"mv\d{3}",
                model_version
            )
        ),
        "INVALID_MODEL_VERSION"
    )

    eval_root = (
        EVAL_ROOT
        + f"run_id={eval_run}/"
    )

    eval_manifest = json_read(
        read,
        eval_root + "evaluation_manifest.json"
    )

    demand(
        eval_manifest.get("status") == "PASSED"
        and eval_manifest.get("evaluation_run_id")
        == eval_run
        and eval_manifest.get("selection_id")
        == selection_id,
        "EVALUATION_MANIFEST_INVALID"
    )

    evaluation_bytes = indexed_artifact(
        read,
        eval_root,
        eval_manifest,
        "evaluation_report.json"
    )

    eval_report = json.loads(
        evaluation_bytes
    )

    demand(
        eval_report.get("status") == "PASSED"
        and eval_report.get("test_evaluated") is True
        and eval_report.get("model_retrained") is False
        and eval_report.get(
            "model_changed_after_test"
        ) is False,
        "EVALUATION_NOT_APPROVED_FOR_PACKAGING"
    )

    demand(
        eval_report.get("selection_id") == selection_id
        and eval_report.get("evaluation_run_id")
        == eval_run,
        "EVALUATION_SELECTION_MISMATCH"
    )

    demand(
        eval_report.get("model_name")
        == choice["model_name"]
        and eval_report.get("model_stage")
        == choice["stage"],
        "EVALUATED_MODEL_CHANGED"
    )

    demand(
        eval_report.get("model_sha256")
        == choice["source_model_sha256"]
        and eval_report.get("model_source_key")
        == choice["source_model_key"],
        "EVALUATED_MODEL_SHA_MISMATCH"
    )

    demand(
        eval_report.get("feature_version") == "fv001"
        and eval_report.get("dataset_version") == "dv001"
        and eval_report.get("feature_count") == 19,
        "EVALUATION_FEATURE_VERSION_MISMATCH"
    )

    card_bytes = indexed_artifact(
        read,
        eval_root,
        eval_manifest,
        "model_card.md"
    )

    demand(
        eval_report.get("test_rows", 0) > 0
        and eval_report["test_metrics"]["mae"] >= 0,
        "INVALID_TEST_METRICS"
    )

    source_root = (
        MODEL_ROOT
        + f"run_id={choice['source_run_id']}/"
    )

    source_manifest = json_read(
        read,
        source_root + "run_manifest.json"
    )

    expected_worker = (
        "m001"
        if choice["stage"] == "M"
        else "n001"
    )

    demand(
        source_manifest.get("status") == "PASSED"
        and source_manifest.get("worker_version")
        == expected_worker
        and source_manifest.get("run_id")
        == choice["source_run_id"],
        "SOURCE_MANIFEST_INVALID"
    )

    source_report = json.loads(
        indexed_artifact(
            read,
            source_root,
            source_manifest,
            "training_report.json"
        )
    )

    demand(
        source_report.get("status") == "PASSED"
        and source_report.get("test_evaluated") is False
        and source_report.get("feature_report_key")
        == choice["feature_report_key"],
        "SOURCE_TRAINING_REPORT_INVALID"
    )

    demand(
        source_report.get("feature_columns")
        == choice["feature_columns"],
        "SOURCE_FEATURE_COLUMN_MISMATCH"
    )

    model_name = (
        f"pipelines/{choice['model_name']}.joblib"
    )

    model_bytes = indexed_artifact(
        read,
        source_root,
        source_manifest,
        model_name
    )

    demand(
        sha256(model_bytes)
        == choice["source_model_sha256"],
        "SELECTED_MODEL_HASH_MISMATCH"
    )

    version_match(source_report)

    feature_manifest = json_read(
        read,
        FEATURE_KEY
    )

    split_manifest = json_read(
        read,
        SPLIT_KEY
    )

    feature_report = json_read(
        read,
        choice["feature_report_key"]
    )

    demand(
        all(
            item.get("status") == "PASSED"
            for item in [
                feature_manifest,
                split_manifest,
                feature_report
            ]
        ),
        "FEATURE_UPSTREAM_FAILED"
    )

    cols = choice["feature_columns"]

    demand(
        feature_manifest.get("model_feature_columns")
        == cols
        and split_manifest.get("model_feature_columns")
        == cols,
        "FEATURE_ORDER_MISMATCH"
    )

    demand(
        feature_manifest.get("feature_version") == "fv001"
        and split_manifest.get("feature_version") == "fv001",
        "FEATURE_VERSION_MISMATCH"
    )

    demand(
        eval_report["test_rows"]
        == split_manifest["split_statistics"]["test"]["rows"],
        "TEST_SIZE_MISMATCH"
    )

    validation_key = (
        SPLIT_PREFIX + "X_validation.parquet"
    )

    split_index = {
        item["key"]: item
        for item in split_manifest["artifacts"]
    }

    demand(
        validation_key in split_index,
        "MISSING_VALIDATION_X"
    )

    validation_bytes = read(
        validation_key
    )

    demand(
        sha256(validation_bytes)
        == split_index[validation_key]["sha256"],
        "VALIDATION_X_SHA_MISMATCH"
    )

    X = pd.read_parquet(
        io.BytesIO(validation_bytes)
    )

    demand(
        len(X) > 0
        and list(X.columns)
        == ["sample_id", "timestamp"] + cols,
        "VALIDATION_X_SCHEMA_MISMATCH"
    )

    prediction_bytes = indexed_artifact(
        read,
        source_root,
        source_manifest,
        "predictions/validation_predictions.parquet"
    )

    prediction_frame = pd.read_parquet(
        io.BytesIO(prediction_bytes)
    )

    sample_id, payload = make_example(
        X,
        cols
    )

    matches = prediction_frame.loc[
        prediction_frame["sample_id"].eq(sample_id)
    ]

    demand(
        len(matches) == 1,
        "SMOKE_SAMPLE_NOT_IN_SOURCE_PREDICTIONS"
    )

    prediction_col = (
        "prediction_" + choice["model_name"]
    )

    demand(
        prediction_col in prediction_frame.columns,
        "SOURCE_PREDICTION_COLUMN_MISSING"
    )

    expected = float(
        matches.iloc[0][prediction_col]
    )

    demand(
        np.isfinite(expected),
        "NON_FINITE_EXPECTED_PREDICTION"
    )

    # Trusted in-account model verified by source SHA-256.
    pipeline = joblib.load(
        io.BytesIO(model_bytes)
    )

    demand(
        isinstance(pipeline, Pipeline),
        "NOT_A_SKLEARN_PIPELINE"
    )

    demand(
        list(pipeline.feature_names_in_) == cols,
        "PIPELINE_INPUT_ORDER_MISMATCH"
    )

    observed = float(
        pipeline.predict(
            pd.DataFrame(
                [payload],
                columns=cols
            )
        )[0]
    )

    demand(
        np.isfinite(observed),
        "NON_FINITE_SMOKE_PREDICTION"
    )

    demand(
        np.isclose(
            observed,
            expected,
            rtol=1e-6,
            atol=1e-5
        ),
        "SMOKE_PREDICTION_MISMATCH"
    )

    package_root = (
        "models/registry-candidates/"
        f"model_id={MODEL_ID}/version={model_version}/"
    )

    environment = {
        "python": platform.python_version(),
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "joblib": joblib.__version__,
        "scipy": pkg_version("scipy"),
        "pyarrow": pkg_version("pyarrow"),
        "xgboost": pkg_version("xgboost"),
        "boto3": pkg_version("boto3")
    }

    requirements = [
        f"numpy=={environment['numpy']}",
        f"pandas=={environment['pandas']}",
        f"scikit-learn=={environment['sklearn']}",
        f"joblib=={environment['joblib']}",
        f"scipy=={environment['scipy']}"
    ]

    if choice["stage"] == "N":
        requirements.append(
            f"xgboost=={environment['xgboost']}"
        )

    requirements_text = (
        "\n".join(requirements) + "\n"
    )

    pip_freeze = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "pip",
            "freeze"
        ],
        text=True
    )

    metadata = {
        "status": "CANDIDATE_PENDING_REGISTRATION",
        "model_id": MODEL_ID,
        "model_version": model_version,
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "selection_id": selection_id,
        "evaluation_run_id": eval_run,
        "training_run_id": choice["source_run_id"],
        "training_stage": choice["stage"],
        "model_name": choice["model_name"],
        "feature_count": len(cols),
        "feature_columns": cols,
        "target": TARGET,
        "model_key": package_root + "model.joblib",
        "model_sha256": sha256(model_bytes),
        "source_model_key": choice["source_model_key"],
        "source_model_sha256": (
            choice["source_model_sha256"]
        ),
        "test_evaluation_status": (
            eval_report["status"]
        ),
        "test_metrics": (
            eval_report["test_metrics"]
        ),
        "train_mae": choice["train_mae"],
        "validation_mae": choice["validation_mae"],
        "environment": environment,
        "inference_interface": "feature-complete-19",
        "postprocessing": (
            "raw_continuous_prediction"
        ),
        "model_retrained_for_packaging": False,
        "limitations": [
            "Historical citywide Seoul bicycle demand, 2017-2018",
            "Weather features from observations rather than forecasts",
            "Original 8-input UI cannot supply all 19 model features",
            "Unregistered candidate; not an approved production deployment"
        ]
    }

    smoke_expected = {
        "sample_id": sample_id,
        "expected_prediction": expected,
        "reloaded_prediction": observed,
        "rtol": 1e-6,
        "atol": 1e-5,
        "source": "validation_predictions.parquet",
        "passed": True
    }

    files = {
        "model.joblib": model_bytes,
        "metadata.json": json_bytes(metadata),
        "evaluation.json": evaluation_bytes,
        "input_schema.json": json_bytes(
            infer_schema(cols)
        ),
        "model_card.md": card_bytes,
        "examples/smoke_input.json": json_bytes({
            "feature_version": "fv001",
            "features": payload
        }),
        "examples/smoke_expected.json": json_bytes(
            smoke_expected
        ),
        "environment/requirements.txt": (
            requirements_text.encode("utf-8")
        ),
        "environment/pip_freeze.txt": (
            pip_freeze.encode("utf-8")
        )
    }

    demand(
        len(files) == 9,
        "PACKAGE_FILE_COUNT_ERROR"
    )

    manifest = {
        "status": "PASSED",
        "model_id": MODEL_ID,
        "model_version": model_version,
        "selection_id": selection_id,
        "evaluation_run_id": eval_run,
        "model_sha256": sha256(model_bytes),
        "files": [
            {
                "path": name,
                "sha256": sha256(content),
                "size_bytes": len(content)
            }
            for name, content in files.items()
        ]
    }

    files["package_manifest.json"] = json_bytes(
        manifest
    )

    return package_root, files, metadata


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--local",
        action="store_true"
    )

    parser.add_argument(
        "--input-dir",
        default="data/package/input"
    )

    parser.add_argument(
        "--output-dir",
        default="data/package/local"
    )

    parser.add_argument(
        "--selection-id",
        default=os.environ.get("SELECTION_ID")
    )

    parser.add_argument(
        "--evaluation-run-id",
        default=os.environ.get("EVALUATION_RUN_ID")
    )

    parser.add_argument(
        "--model-version",
        default=os.environ.get(
            "MODEL_VERSION", "mv001"
        )
    )

    args = parser.parse_args()

    safe_id(
        args.selection_id,
        "SELECTION_ID"
    )

    safe_id(
        args.evaluation_run_id,
        "EVALUATION_RUN_ID"
    )

    if args.local:
        root = Path(args.input_dir)
        output = Path(args.output_dir)

        def read(key):
            return (
                root / key
            ).read_bytes()

        def write(key, content):
            target = output / key

            demand(
                not target.exists(),
                "OUTPUT_ALREADY_EXISTS: " + key
            )

            target.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            target.write_bytes(content)

            demand(
                sha256(target.read_bytes())
                == sha256(content),
                "OUTPUT_WRITE_VERIFY_FAILED: " + key
            )

    else:
        bucket = os.environ["BIKEPULSE_BUCKET"]
        s3 = boto3.client("s3")

        def read(key):
            return s3.get_object(
                Bucket=bucket,
                Key=key
            )["Body"].read()

        def write(key, content):
            if key.endswith(".json"):
                content_type = "application/json"
            elif key.endswith((".md", ".txt")):
                content_type = "text/plain; charset=utf-8"
            else:
                content_type = "application/octet-stream"

            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                IfNoneMatch="*"
            )

            demand(
                sha256(read(key))
                == sha256(content),
                "S3_UPLOAD_VERIFY_FAILED: " + key
            )

    try:
        prefix, files, metadata = build(
            read,
            args.selection_id,
            args.evaluation_run_id,
            args.model_version
        )

        for name, content in files.items():
            write(
                prefix + name,
                content
            )

            print(
                "MODEL_PACKAGE_SAVED:",
                prefix + name,
                flush=True
            )

        print(
            "MODEL_PACKAGE_STATUS: PASSED",
            flush=True
        )

        print(
            "MODEL_PACKAGE_MODEL:",
            metadata["model_name"],
            flush=True
        )

        print(
            "MODEL_PACKAGE_SHA256:",
            metadata["model_sha256"],
            flush=True
        )

        return 0

    except Exception as exc:
        print(
            "MODEL_PACKAGE_FAILED:",
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
