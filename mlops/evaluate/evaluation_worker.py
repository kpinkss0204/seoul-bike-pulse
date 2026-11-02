import argparse
import hashlib
import io
import json
import os
import re
import sys

from datetime import datetime, timezone
from pathlib import Path

import boto3
import joblib
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
import xgboost

from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score
)


FEATURE_KEY = (
    "features/version=fv001/feature_manifest.json"
)

SPLIT_PREFIX = (
    "splits/version=dv001/feature_version=fv001/"
)

SPLIT_KEY = SPLIT_PREFIX + "split_manifest.json"

BASE = (
    "models/experiments/feature_version=fv001/"
)

SELECT_BASE = (
    "models/selections/feature_version=fv001/"
)

EVAL_BASE = (
    "models/evaluations/feature_version=fv001/"
)

ALLOWED = {
    "M": {"dummy", "ridge", "random_forest"},
    "N": {"xgb_default", "xgb_grid", "xgb_random"}
}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def safe_id(value, label):
    check(
        bool(
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
                value
            )
        ),
        "INVALID_" + label
    )
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(obj):
    return (
        json.dumps(
            obj,
            indent=2,
            ensure_ascii=False,
            allow_nan=False
        ) + "\n"
    ).encode("utf-8")


def load_json(read, key):
    return json.loads(
        read(key).decode("utf-8")
    )


def verify_artifact(read, prefix, manifest, name):
    items = {
        item["path"]: item
        for item in manifest["artifacts"]
    }

    check(
        name in items,
        "ARTIFACT_NOT_LISTED: " + name
    )

    content = read(prefix + name)

    check(
        digest(content) == items[name]["sha256"],
        "ARTIFACT_SHA256_MISMATCH: " + name
    )

    check(
        len(content) == items[name]["size_bytes"],
        "ARTIFACT_SIZE_MISMATCH: " + name
    )

    return content


def experiment(read, stage, run_id):
    safe_id(run_id, "RUN_ID")

    prefix = BASE + f"run_id={run_id}/"

    manifest = load_json(
        read,
        prefix + "run_manifest.json"
    )

    check(
        manifest.get("status") == "PASSED"
        and manifest.get("worker_version")
        == stage.lower() + "001"
        and manifest.get("run_id") == run_id,
        "BAD_EXPERIMENT_MANIFEST: " + stage
    )

    report = json.loads(
        verify_artifact(
            read,
            prefix,
            manifest,
            "training_report.json"
        )
    )

    check(
        report.get("status") == "PASSED"
        and report.get("test_evaluated") is False
        and report.get("dataset_version") == "dv001"
        and report.get("feature_version") == "fv001",
        "BAD_EXPERIMENT_REPORT: " + stage
    )

    return {
        "prefix": prefix,
        "manifest": manifest,
        "report": report
    }


def prepare_selection(
    read,
    m_run,
    n_run,
    selection_id,
    feature_report_key
):
    safe_id(selection_id, "SELECTION_ID")

    m = experiment(read, "M", m_run)
    n = experiment(read, "N", n_run)

    feature_manifest = load_json(
        read,
        FEATURE_KEY
    )

    split_manifest = load_json(
        read,
        SPLIT_KEY
    )

    prior = load_json(
        read,
        feature_report_key
    )

    check(
        feature_manifest.get("status") == "PASSED"
        and split_manifest.get("status") == "PASSED"
        and prior.get("status") == "PASSED",
        "UPSTREAM_NOT_PASSED"
    )

    check(
        feature_manifest.get("feature_version") == "fv001"
        and split_manifest.get("feature_version") == "fv001"
        and feature_manifest.get("dataset_version") == "dv001"
        and split_manifest.get("dataset_version") == "dv001",
        "VERSION_MISMATCH"
    )

    check(
        m["report"].get("feature_report_key")
        == feature_report_key
        and n["report"].get("feature_report_key")
        == feature_report_key,
        "INPUT_LINEAGE_MISMATCH"
    )

    cols = feature_manifest["model_feature_columns"]

    check(
        len(cols) == 19
        and len(set(cols)) == 19
        and cols == split_manifest["model_feature_columns"]
        and cols == m["report"]["feature_columns"]
        and cols == n["report"]["feature_columns"],
        "FEATURE_COLUMNS_MISMATCH"
    )

    check(
        m["report"]["train_rows"] == n["report"]["train_rows"]
        and m["report"]["validation_rows"]
        == n["report"]["validation_rows"],
        "SPLIT_ROW_COUNT_MISMATCH"
    )

    check(
        n["report"]["baseline_run_id"] == m_run,
        "BASELINE_RUN_ID_MISMATCH"
    )

    baseline = json.loads(
        verify_artifact(
            read,
            m["prefix"],
            m["manifest"],
            "metrics/comparison.json"
        )
    )

    combined = json.loads(
        verify_artifact(
            read,
            n["prefix"],
            n["manifest"],
            "comparison/combined_comparison.json"
        )
    )

    check(
        baseline.get("test_evaluated") is False
        and combined.get("test_evaluated") is False
        and combined.get("baseline_run_id") == m_run,
        "COMPARISON_LINEAGE_MISMATCH"
    )

    models = combined["models"]

    expected = {
        (stage, name)
        for stage, names in ALLOWED.items()
        for name in names
    }

    check(
        len(models) == 6
        and {
            (item["stage"], item["model"])
            for item in models
        } == expected,
        "INVALID_MODEL_SET"
    )

    baseline_map = {
        item["model"]: item
        for item in baseline["models"]
    }

    for item in models:
        if item["stage"] == "M":
            original = baseline_map[item["model"]]

            check(
                item["validation_mae"]
                == original["validation_mae"]
                and item["validation_rmse"]
                == original["validation_rmse"],
                "BASELINE_METRICS_MISMATCH"
            )

        check(
            np.isfinite(
                float(item["validation_mae"])
            )
            and np.isfinite(
                float(item["validation_rmse"])
            ),
            "INVALID_VALIDATION_METRICS"
        )

    leader = min(
        models,
        key=lambda item: (
            item["validation_mae"],
            item["validation_rmse"]
        )
    )

    check(
        combined.get("provisional_overall_leader")
        == {
            "stage": leader["stage"],
            "model": leader["model"]
        },
        "LEADER_DISAGREEMENT"
    )

    selected = (
        m if leader["stage"] == "M"
        else n
    )

    model_path = (
        f"pipelines/{leader['model']}.joblib"
    )

    index = {
        item["path"]: item
        for item in selected["manifest"]["artifacts"]
    }

    check(
        model_path in index,
        "SELECTED_MODEL_NOT_LISTED"
    )

    verify_artifact(
        read,
        selected["prefix"],
        selected["manifest"],
        model_path
    )

    choice = {
        "status": "LOCKED",
        "selection_id": selection_id,
        "created_at_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "baseline_run_id": m_run,
        "xgboost_run_id": n_run,
        "feature_report_key": feature_report_key,
        "feature_columns": cols,
        "split_manifest_key": SPLIT_KEY,
        "stage": leader["stage"],
        "model_name": leader["model"],
        "validation_mae": leader["validation_mae"],
        "validation_rmse": leader["validation_rmse"],
        "validation_r2": leader["validation_r2"],
        "train_mae": leader["train_mae"],
        "source_run_id": (
            m_run if leader["stage"] == "M"
            else n_run
        ),
        "source_model_key": (
            selected["prefix"] + model_path
        ),
        "source_model_sha256": (
            index[model_path]["sha256"]
        ),
        "source_manifest_key": (
            selected["prefix"] + "run_manifest.json"
        ),
        "sklearn_version": (
            selected["report"]["sklearn_version"]
        ),
        "numpy_version": (
            selected["report"]["numpy_version"]
        ),
        "pandas_version": (
            selected["report"]["pandas_version"]
        ),
        "joblib_version": (
            selected["report"]["joblib_version"]
        ),
        "xgboost_version": (
            selected["report"].get("xgboost_version")
        ),
        "selection_metric": "validation_mae",
        "test_evaluated": False,
        "all_candidates": models
    }

    return choice


def selection_prefix(selection_id):
    return (
        SELECT_BASE
        + f"selection_id={safe_id(selection_id, 'SELECTION_ID')}/"
    )


def verify_selection(read, selection_id):
    prefix = selection_prefix(selection_id)

    selection_bytes = read(
        prefix + "selection.json"
    )

    manifest = load_json(
        read,
        prefix + "selection_manifest.json"
    )

    check(
        manifest.get("status") == "PASSED"
        and manifest.get("selection_id") == selection_id
        and manifest.get("selection_sha256")
        == digest(selection_bytes),
        "SELECTION_MANIFEST_MISMATCH"
    )

    choice = json.loads(selection_bytes)

    check(
        choice.get("status") == "LOCKED"
        and choice.get("selection_id") == selection_id
        and choice.get("test_evaluated") is False,
        "INVALID_LOCKED_SELECTION"
    )

    check(
        choice["stage"] in ALLOWED
        and choice["model_name"]
        in ALLOWED[choice["stage"]],
        "MODEL_NOT_ALLOWED"
    )

    expected = (
        BASE
        + f"run_id={safe_id(choice['source_run_id'], 'SOURCE_RUN_ID')}/"
        + f"pipelines/{choice['model_name']}.joblib"
    )

    check(
        choice["source_model_key"] == expected,
        "INVALID_MODEL_PATH"
    )

    return choice


def metrics(y, pred):
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)

    check(
        len(y) > 1
        and np.isfinite(y).all()
        and np.isfinite(pred).all(),
        "INVALID_TEST_VALUES"
    )

    denominator = np.sum(np.abs(y))

    return {
        "mae": round(
            float(mean_absolute_error(y, pred)), 6
        ),
        "rmse": round(
            float(
                np.sqrt(
                    mean_squared_error(y, pred)
                )
            ),
            6
        ),
        "r2": round(
            float(r2_score(y, pred)), 6
        ),
        "bias_pred_minus_actual": round(
            float(np.mean(pred - y)), 6
        ),
        "wape_percent": (
            round(
                float(
                    np.sum(np.abs(pred - y))
                    / denominator * 100
                ),
                4
            )
            if denominator > 0 else None
        ),
        "negative_predictions": int(
            np.sum(pred < 0)
        )
    }


def jpg(fig):
    buffer = io.BytesIO()

    fig.tight_layout()

    fig.savefig(
        buffer,
        format="jpeg",
        dpi=145,
        bbox_inches="tight",
        facecolor="white",
        pil_kwargs={"quality": 88}
    )

    plt.close(fig)

    return buffer.getvalue()


def make_charts(table):
    plt.rcParams.update({
        "figure.figsize": (10, 5),
        "font.size": 10
    })

    artifacts = {}

    # A. First 168 functioning observations
    first = table.head(168)

    fig, ax = plt.subplots(figsize=(12, 4.8))

    ax.plot(
        first["timestamp"],
        first["actual_rentals"],
        label="Actual"
    )

    ax.plot(
        first["timestamp"],
        first["prediction"],
        label="Predicted"
    )

    ax.set(
        title="Test: first 168 functioning observations",
        xlabel="Timestamp",
        ylabel="Bike rentals"
    )

    ax.legend()
    fig.autofmt_xdate()

    artifacts[
        "charts/01_actual_vs_prediction.jpg"
    ] = jpg(fig)

    # B. Actual versus predicted scatter
    fig, ax = plt.subplots(figsize=(7, 6))

    ax.scatter(
        table["actual_rentals"],
        table["prediction"],
        s=8,
        alpha=0.35
    )

    bounds = [
        float(min(
            table["actual_rentals"].min(),
            table["prediction"].min()
        )),
        float(max(
            table["actual_rentals"].max(),
            table["prediction"].max()
        ))
    ]

    ax.plot(
        bounds, bounds,
        "--",
        color="black"
    )

    ax.set(
        title="Actual versus predicted on Test",
        xlabel="Actual",
        ylabel="Prediction"
    )

    artifacts[
        "charts/02_actual_vs_pred_scatter.jpg"
    ] = jpg(fig)

    # C. Residual distribution
    fig, ax = plt.subplots(figsize=(9, 4.5))

    ax.hist(
        table["residual_actual_minus_prediction"],
        bins=35
    )

    ax.axvline(
        0,
        color="black",
        linestyle="--"
    )

    ax.set(
        title="Test residual distribution",
        xlabel="Actual - prediction",
        ylabel="Observations"
    )

    artifacts[
        "charts/03_residual_distribution.jpg"
    ] = jpg(fig)

    # D. Hourly MAE
    hourly = (
        table.groupby("hour")["absolute_error"]
        .mean()
        .reindex(range(24))
    )

    fig, ax = plt.subplots(figsize=(11, 4.5))

    ax.bar(
        hourly.index,
        hourly.values
    )

    ax.set(
        title="Test MAE by hour",
        xlabel="Hour",
        ylabel="MAE"
    )

    ax.set_xticks(range(0, 24, 2))

    artifacts[
        "charts/04_hourly_mae.jpg"
    ] = jpg(fig)

    # E. Seasonal MAE
    seasonal = (
        table.groupby("season")["absolute_error"]
        .mean()
        .reindex([
            "winter", "spring", "summer", "autumn"
        ])
        .dropna()
    )

    fig, ax = plt.subplots(figsize=(8, 4.5))

    ax.bar(
        seasonal.index,
        seasonal.values
    )

    ax.set(
        title="Test MAE by season",
        xlabel="Season",
        ylabel="MAE"
    )

    artifacts[
        "charts/05_seasonal_mae.jpg"
    ] = jpg(fig)

    # F. Rainfall MAE
    rainfall = (
        table.groupby(
            "rain_group",
            observed=True
        )["absolute_error"]
        .mean()
        .reindex([
            "No rain",
            "0-1 mm",
            "1-5 mm",
            ">5 mm"
        ])
        .dropna()
    )

    fig, ax = plt.subplots(figsize=(9, 4.5))

    ax.bar(
        rainfall.index,
        rainfall.values
    )

    ax.set(
        title="Test MAE by rainfall group",
        xlabel="Rainfall in hour",
        ylabel="MAE"
    )

    artifacts[
        "charts/06_rainfall_mae.jpg"
    ] = jpg(fig)

    return artifacts


def group_summary(frame, column):
    rows = []

    for key, group in frame.groupby(
        column,
        observed=True
    ):
        rows.append({
            "group": str(key),
            "rows": int(len(group)),
            "mae": round(
                float(group["absolute_error"].mean()),
                6
            ),
            "bias": round(
                float(
                    (
                        group["prediction"]
                        - group["actual_rentals"]
                    ).mean()
                ),
                6
            )
        })

    return rows


def evaluate(read, selection_id, evaluation_run_id):
    safe_id(
        evaluation_run_id,
        "EVALUATION_RUN_ID"
    )

    choice = verify_selection(
        read,
        selection_id
    )

    # Verify selected source model before opening Test.
    source = experiment(
        read,
        choice["stage"],
        choice["source_run_id"]
    )

    model_rel = (
        "pipelines/"
        + choice["model_name"]
        + ".joblib"
    )

    model_bytes = verify_artifact(
        read,
        source["prefix"],
        source["manifest"],
        model_rel
    )

    check(
        digest(model_bytes)
        == choice["source_model_sha256"],
        "LOCKED_MODEL_SHA256_MISMATCH"
    )

    feature_manifest = load_json(
        read,
        FEATURE_KEY
    )

    split_manifest = load_json(
        read,
        SPLIT_KEY
    )

    prior = load_json(
        read,
        choice["feature_report_key"]
    )

    check(
        feature_manifest.get("status") == "PASSED"
        and split_manifest.get("status") == "PASSED"
        and prior.get("status") == "PASSED",
        "UPSTREAM_NOT_PASSED"
    )

    cols = choice["feature_columns"]

    check(
        cols == feature_manifest["model_feature_columns"]
        == split_manifest["model_feature_columns"]
        and len(cols) == 19,
        "FEATURE_SCHEMA_CHANGED"
    )

    check(
        source["report"]["feature_report_key"]
        == choice["feature_report_key"],
        "SELECTED_MODEL_LINEAGE_MISMATCH"
    )

    runtime = {
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "joblib_version": joblib.__version__,
        "xgboost_version": xgboost.__version__
    }

    for package in [
        "sklearn_version",
        "numpy_version",
        "pandas_version",
        "joblib_version"
    ]:
        check(
            str(choice[package]) == str(runtime[package]),
            f"RUNTIME_VERSION_MISMATCH: {package}"
        )

    if choice["stage"] == "N":
        check(
            choice["xgboost_version"]
            == runtime["xgboost_version"],
            "XGBOOST_RUNTIME_VERSION_MISMATCH"
        )

    # Only trusted, SHA-256 verified Joblib objects.
    pipeline = joblib.load(
        io.BytesIO(model_bytes)
    )

    artifact_index = {
        item["key"]: item
        for item in split_manifest["artifacts"]
    }

    frames = {}

    # Test data is first read in this evaluate mode.
    for name in [
        "X_test.parquet",
        "y_test.parquet"
    ]:
        key = SPLIT_PREFIX + name

        check(
            key in artifact_index,
            "MISSING_SPLIT_ARTIFACT: " + name
        )

        content = read(key)

        check(
            digest(content)
            == artifact_index[key]["sha256"],
            "TEST_SPLIT_SHA256_MISMATCH: " + name
        )

        frames[name] = pd.read_parquet(
            io.BytesIO(content)
        )

    X = frames["X_test.parquet"]
    y = frames["y_test.parquet"]

    check(
        list(X.columns)
        == ["sample_id", "timestamp"] + cols,
        "TEST_X_COLUMNS_MISMATCH"
    )

    check(
        list(y.columns)
        == ["sample_id", "rented_bike_count"],
        "TEST_Y_COLUMNS_MISMATCH"
    )

    check(
        len(X) == len(y) > 1
        and len(X)
        == split_manifest["split_statistics"]["test"]["rows"],
        "TEST_ROW_COUNT_MISMATCH"
    )

    check(
        X["sample_id"].equals(
            y["sample_id"]
        )
        and X["sample_id"].is_unique,
        "TEST_ID_ALIGNMENT_FAILED"
    )

    times = pd.to_datetime(
        X["timestamp"],
        errors="raise"
    )

    check(
        times.is_monotonic_increasing
        and times.is_unique,
        "TEST_TIME_ORDER_FAILED"
    )

    boundaries = split_manifest["boundaries"]

    check(
        times.min().normalize()
        >= pd.Timestamp(boundaries["test_start"])
        and times.max().normalize()
        <= pd.Timestamp(boundaries["test_end"])
        and times.min().normalize()
        > pd.Timestamp(boundaries["validation_end"]),
        "TEST_DATE_BOUNDARY_FAILED"
    )

    check(
        X[cols].notna().all().all()
        and y["rented_bike_count"].notna().all(),
        "TEST_NULL_VALUES"
    )

    check(
        np.isfinite(
            y["rented_bike_count"].to_numpy(
                dtype=float
            )
        ).all()
        and (y["rented_bike_count"] >= 0).all(),
        "INVALID_TEST_TARGET"
    )

    print(
        "LOCKED_SELECTION_VERIFIED:",
        choice["model_name"],
        flush=True
    )

    print(
        "TEST_INPUT_VERIFIED:",
        len(X),
        flush=True
    )

    prediction = np.asarray(
        pipeline.predict(
            X[cols]
        ),
        dtype=float
    )

    score = metrics(
        y["rented_bike_count"].to_numpy(),
        prediction
    )

    out = pd.DataFrame({
        "sample_id": X["sample_id"],
        "timestamp": times,
        "hour": X["hour"],
        "season": X["season"],
        "rainfall_mm": X["rainfall_mm"],
        "actual_rentals": (
            y["rented_bike_count"].to_numpy()
        ),
        "prediction": prediction
    })

    out["absolute_error"] = np.abs(
        out["actual_rentals"]
        - out["prediction"]
    )

    out["residual_actual_minus_prediction"] = (
        out["actual_rentals"]
        - out["prediction"]
    )

    out["rain_group"] = pd.cut(
        out["rainfall_mm"],
        bins=[
            -0.001, 0, 1, 5, np.inf
        ],
        labels=[
            "No rain",
            "0-1 mm",
            "1-5 mm",
            ">5 mm"
        ]
    )

    errors = {
        "by_hour": group_summary(
            out, "hour"
        ),
        "by_season": group_summary(
            out, "season"
        ),
        "by_rainfall": group_summary(
            out, "rain_group"
        )
    }

    artifacts = make_charts(out)

    out["rain_group"] = (
        out["rain_group"].astype(str)
    )

    prediction_buffer = io.BytesIO()

    out.to_parquet(
        prediction_buffer,
        index=False,
        compression="snappy",
        engine="pyarrow"
    )

    artifacts[
        "predictions/test_predictions.parquet"
    ] = prediction_buffer.getvalue()

    artifacts[
        "error_analysis.json"
    ] = encode(errors)

    artifacts[
        "comparison_snapshot.json"
    ] = encode({
        "selection_id": selection_id,
        "selection_metric": "validation_mae",
        "models": choice["all_candidates"],
        "locked_model": choice["model_name"],
        "locked_stage": choice["stage"]
    })

    report = {
        "status": "PASSED",
        "worker_version": "o001",
        "evaluation_run_id": evaluation_run_id,
        "selection_id": selection_id,
        "created_at_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "model_stage": choice["stage"],
        "model_name": choice["model_name"],
        "model_source_key": (
            choice["source_model_key"]
        ),
        "model_sha256": (
            choice["source_model_sha256"]
        ),
        "feature_count": len(cols),
        "test_rows": len(X),
        "test_start_observed": (
            times.min().isoformat()
        ),
        "test_end_observed": (
            times.max().isoformat()
        ),
        "train_mae": choice["train_mae"],
        "selection_validation_mae": (
            choice["validation_mae"]
        ),
        "test_metrics": score,
        "test_evaluated": True,
        "model_retrained": False,
        "model_changed_after_test": False,
        "evaluation_limitations": [
            "Historical 2017-2018 holdout",
            "Observed weather features",
            "Test labels must not guide model selection"
        ]
    }

    artifacts[
        "evaluation_report.json"
    ] = encode(report)

    card = (
        "# SEOUL BIKE PULSE - Final Test Model Card\n\n"
        f"Selection: {selection_id}\n\n"
        f"Model: {choice['stage']} / {choice['model_name']}\n\n"
        "Feature version: fv001\n\n"
        f"Test rows: {len(X)}\n\n"
        f"Validation MAE: {choice['validation_mae']}\n\n"
        f"Test MAE: {score['mae']}\n\n"
        f"Test RMSE: {score['rmse']}\n\n"
        f"Test R2: {score['r2']}\n\n"
        "Historical holdout with observed weather.\n"
        "No model retraining or deployment was performed.\n"
    )

    artifacts[
        "model_card.md"
    ] = card.encode("utf-8")

    check(
        len(artifacts) == 11,
        "OUTPUT_ARTIFACT_COUNT_MISMATCH"
    )

    completion = {
        "status": "PASSED",
        "worker_version": "o001",
        "evaluation_run_id": evaluation_run_id,
        "selection_id": selection_id,
        "created_at_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "artifacts": [
            {
                "path": path,
                "sha256": digest(contents),
                "size_bytes": len(contents)
            }
            for path, contents in artifacts.items()
        ]
    }

    artifacts[
        "evaluation_manifest.json"
    ] = encode(completion)

    print(
        "TEST_METRICS:",
        json.dumps(score),
        flush=True
    )

    return artifacts


def save(
    read,
    write,
    mode,
    selection_id,
    m_run,
    n_run,
    feature_report_key,
    evaluation_run_id
):
    if mode == "lock":
        choice = prepare_selection(
            read,
            m_run,
            n_run,
            selection_id,
            feature_report_key
        )

        prefix = selection_prefix(
            selection_id
        )

        content = encode(choice)

        manifest = encode({
            "status": "PASSED",
            "selection_id": selection_id,
            "selection_sha256": digest(content)
        })

        write(
            prefix + "selection.json",
            content
        )

        write(
            prefix + "selection_manifest.json",
            manifest
        )

        print(
            "MODEL_SELECTION_LOCKED:",
            choice["stage"],
            choice["model_name"],
            flush=True
        )

    elif mode == "evaluate":
        artifacts = evaluate(
            read,
            selection_id,
            evaluation_run_id
        )

        prefix = (
            EVAL_BASE
            + f"run_id={safe_id(evaluation_run_id, 'EVALUATION_RUN_ID')}/"
        )

        # evaluation_manifest.json is the last item.
        for name, content in artifacts.items():
            write(
                prefix + name,
                content
            )

            print(
                "EVALUATION_ARTIFACT_SAVED:",
                prefix + name,
                flush=True
            )

        print(
            "EVALUATION_STATUS: PASSED",
            flush=True
        )

    else:
        raise ValueError("INVALID_MODE")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--local",
        action="store_true"
    )

    parser.add_argument(
        "--mode",
        choices=["lock", "evaluate"],
        required=True
    )

    parser.add_argument(
        "--input-dir",
        default="data/evaluation/input"
    )

    parser.add_argument(
        "--output-dir",
        default="data/evaluation/local"
    )

    parser.add_argument(
        "--selection-id",
        default=os.environ.get("SELECTION_ID")
    )

    parser.add_argument(
        "--m-run-id",
        default=os.environ.get("BASELINE_RUN_ID")
    )

    parser.add_argument(
        "--n-run-id",
        default=os.environ.get("XGB_RUN_ID")
    )

    parser.add_argument(
        "--feature-report-key",
        default=os.environ.get("FEATURE_REPORT_KEY")
    )

    parser.add_argument(
        "--evaluation-run-id",
        default=os.environ.get("EVALUATION_RUN_ID")
    )

    args = parser.parse_args()

    check(
        args.selection_id,
        "SELECTION_ID_REQUIRED"
    )

    if args.local:
        root = Path(
            args.input_dir
        )

        output = Path(
            args.output_dir
        )

        def read(key):
            path = (
                output / key
                if key.startswith(SELECT_BASE)
                else root / key
            )

            return path.read_bytes()

        def write(key, data):
            path = output / key

            check(
                not path.exists(),
                "LOCAL_OUTPUT_ALREADY_EXISTS: " + key
            )

            path.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            path.write_bytes(data)

    else:
        bucket = os.environ["BIKEPULSE_BUCKET"]

        s3 = boto3.client("s3")

        def read(key):
            return s3.get_object(
                Bucket=bucket,
                Key=key
            )["Body"].read()

        def write(key, data):
            if key.endswith(".json"):
                content_type = "application/json"
            elif key.endswith(".jpg"):
                content_type = "image/jpeg"
            elif key.endswith(".md"):
                content_type = "text/markdown"
            else:
                content_type = "application/octet-stream"

            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                IfNoneMatch="*"
            )

            check(
                digest(read(key)) == digest(data),
                "UPLOAD_VERIFY_FAILED: " + key
            )

    try:
        save(
            read,
            write,
            args.mode,
            args.selection_id,
            args.m_run_id,
            args.n_run_id,
            args.feature_report_key,
            args.evaluation_run_id
        )

    except Exception as exc:
        print(
            "MODEL_EVALUATION_FAILED:",
            type(exc).__name__,
            str(exc),
            flush=True
        )

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
