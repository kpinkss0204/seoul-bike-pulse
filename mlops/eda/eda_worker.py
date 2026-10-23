import argparse
import hashlib
import io
import json
import os
import re
import sys

from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import seaborn as sns


EXPECTED_COLUMNS = [
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

SEASON_ORDER = [
    "winter", "spring", "summer", "autumn"
]

WEEKDAY_ORDER = [
    "Mon", "Tue", "Wed", "Thu",
    "Fri", "Sat", "Sun"
]

RAIN_ORDER = [
    "No rain", "0-1 mm", "1-5 mm", ">5 mm"
]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def check_lineage(parquet_bytes, report, curated_key):
    actual_hash = sha256_bytes(parquet_bytes)

    if report.get("status") != "PASSED":
        raise ValueError(
            "TRANSFORMATION_REPORT_NOT_PASSED"
        )

    if report.get("output_key") != curated_key:
        raise ValueError(
            "TRANSFORMATION_OUTPUT_KEY_MISMATCH"
        )

    if report.get("output", {}).get(
        "parquet_sha256"
    ) != actual_hash:
        raise ValueError(
            "TRANSFORMATION_PARQUET_SHA256_MISMATCH"
        )

    if report.get("output", {}).get("row_count") != 8760:
        raise ValueError(
            "TRANSFORMATION_ROW_COUNT_MISMATCH"
        )

    if report.get("output", {}).get("column_count") != 14:
        raise ValueError(
            "TRANSFORMATION_COLUMN_COUNT_MISMATCH"
        )

    return actual_hash


def prepare_dataframe(parquet_bytes):
    table = pq.read_table(
        pa.BufferReader(parquet_bytes)
    )

    if (
        table.num_rows != 8760
        or table.schema.names != EXPECTED_COLUMNS
    ):
        raise ValueError(
            "CURATED_PARQUET_SCHEMA_MISMATCH"
        )

    df = table.to_pandas()

    if df.isna().any().any():
        raise ValueError(
            "CURATED_PARQUET_HAS_NULLS"
        )

    if not set(df["season"]).issubset(
        set(SEASON_ORDER)
    ):
        raise ValueError(
            "CURATED_SEASON_VALUES_INVALID"
        )

    if not df["hour"].between(0, 23).all():
        raise ValueError(
            "CURATED_HOUR_INVALID"
        )

    if (df["rented_bike_count"] < 0).any():
        raise ValueError(
            "CURATED_RENTALS_INVALID"
        )

    df["date"] = pd.to_datetime(
        df["date"],
        errors="raise"
    )

    if (
        df["date"].min() != pd.Timestamp("2017-12-01")
        or df["date"].max() != pd.Timestamp("2018-11-30")
    ):
        raise ValueError(
            "CURATED_DATE_RANGE_MISMATCH"
        )

    if df.duplicated(["date", "hour"]).any():
        raise ValueError(
            "CURATED_DUPLICATE_HOUR"
        )

    df["timestamp"] = (
        df["date"]
        + pd.to_timedelta(df["hour"], unit="h")
    )

    df["weekday_number"] = (
        df["date"].dt.dayofweek
    )

    df["weekday_name"] = (
        df["weekday_number"].map(
            dict(enumerate(WEEKDAY_ORDER))
        )
    )

    df["is_weekend"] = (
        df["weekday_number"] >= 5
    )

    df["month"] = df["date"].dt.month

    active = df.loc[
        df["is_functioning_day"] == True
    ].copy()

    if active.empty:
        raise ValueError(
            "NO_FUNCTIONING_OBSERVATIONS"
        )

    active["rainfall_group"] = pd.cut(
        active["rainfall_mm"],
        bins=[
            -0.001, 0, 1, 5, float("inf")
        ],
        labels=RAIN_ORDER
    )

    return df, active


def save_jpg(fig):
    buffer = io.BytesIO()

    fig.tight_layout()

    fig.savefig(
        buffer,
        format="jpeg",
        dpi=150,
        bbox_inches="tight",
        facecolor="white",
        pil_kwargs={
            "quality": 88
        }
    )

    plt.close(fig)

    return buffer.getvalue()


def make_plots(df, active):
    sns.set_theme(
        style="whitegrid",
        context="notebook"
    )

    charts = {}

    # A. Hourly demand
    hourly = (
        active.groupby("hour")["rented_bike_count"]
        .mean()
        .reindex(range(24))
    )

    fig, ax = plt.subplots(figsize=(11, 4.8))

    ax.plot(
        hourly.index,
        hourly.values,
        marker="o"
    )

    ax.set(
        title="Average hourly bike rentals",
        xlabel="Hour",
        ylabel="Average rentals"
    )

    ax.set_xticks(range(0, 24, 2))

    charts["01_hourly_demand.jpg"] = save_jpg(fig)

    # B. Weekday demand
    weekdays = (
        active.groupby("weekday_name")[
            "rented_bike_count"
        ]
        .mean()
        .reindex(WEEKDAY_ORDER)
    )

    fig, ax = plt.subplots(figsize=(9, 4.8))

    ax.bar(
        weekdays.index,
        weekdays.values
    )

    ax.set(
        title="Average rentals by weekday",
        xlabel="Weekday",
        ylabel="Average rentals"
    )

    charts["02_weekday_demand.jpg"] = save_jpg(fig)

    # C. Seasonal demand
    seasons = (
        active.groupby("season")[
            "rented_bike_count"
        ]
        .mean()
        .reindex(SEASON_ORDER)
    )

    fig, ax = plt.subplots(figsize=(9, 4.8))

    ax.bar(
        seasons.index,
        seasons.values
    )

    ax.set(
        title="Average rentals by season",
        xlabel="Season",
        ylabel="Average rentals"
    )

    charts["03_seasonal_demand.jpg"] = save_jpg(fig)

    # D. Temperature versus rentals
    fig, ax = plt.subplots(figsize=(10, 5))

    hex_plot = ax.hexbin(
        active["temperature_c"],
        active["rented_bike_count"],
        gridsize=42,
        mincnt=1,
        cmap="viridis"
    )

    fig.colorbar(
        hex_plot,
        ax=ax,
        label="Number of hours"
    )

    ax.set(
        title="Temperature and observed bike rentals",
        xlabel="Temperature (C)",
        ylabel="Hourly rentals"
    )

    charts["04_temperature_demand.jpg"] = save_jpg(fig)

    # E. Rainfall group demand
    rain = (
        active.groupby(
            "rainfall_group",
            observed=False
        )["rented_bike_count"]
        .mean()
        .reindex(RAIN_ORDER)
    )

    fig, ax = plt.subplots(figsize=(9, 4.8))

    ax.bar(
        rain.index,
        rain.values
    )

    ax.set(
        title="Average rentals by rainfall group",
        xlabel="Rainfall per hour",
        ylabel="Average rentals"
    )

    charts["05_rainfall_demand.jpg"] = save_jpg(fig)

    # F. Hour-season heatmap
    pivot = (
        active.pivot_table(
            index="hour",
            columns="season",
            values="rented_bike_count",
            aggfunc="mean",
            observed=False
        )
        .reindex(
            index=range(24),
            columns=SEASON_ORDER
        )
    )

    fig, ax = plt.subplots(figsize=(10, 7))

    sns.heatmap(
        pivot,
        cmap="YlGnBu",
        ax=ax,
        cbar_kws={
            "label": "Average rentals"
        }
    )

    ax.set(
        title="Hourly demand across seasons",
        xlabel="Season",
        ylabel="Hour"
    )

    charts["06_hour_season_heatmap.jpg"] = save_jpg(fig)

    # G. Numeric correlation heatmap
    numeric_columns = [
        "rented_bike_count",
        "hour",
        "temperature_c",
        "humidity_pct",
        "wind_speed_mps",
        "visibility_10m",
        "dew_point_temperature_c",
        "solar_radiation_mj_m2",
        "rainfall_mm",
        "snowfall_cm"
    ]

    correlation = (
        active[numeric_columns]
        .corr(numeric_only=True)
    )

    fig, ax = plt.subplots(figsize=(11, 8))

    sns.heatmap(
        correlation,
        vmin=-1,
        vmax=1,
        center=0,
        cmap="coolwarm",
        ax=ax
    )

    ax.set_title(
        "Pearson correlation (functioning hours)"
    )

    charts["07_correlation_heatmap.jpg"] = save_jpg(fig)

    # H. Daily total and moving average
    daily = (
        active.groupby("date")[
            "rented_bike_count"
        ]
        .sum()
        .sort_index()
    )

    fig, ax = plt.subplots(figsize=(12, 4.8))

    ax.plot(
        daily.index,
        daily.values,
        alpha=0.45,
        label="Daily total"
    )

    ax.plot(
        daily.index,
        daily.rolling(
            7,
            min_periods=1
        ).mean(),
        label="7-day moving average"
    )

    ax.set(
        title="Daily rentals on functioning hours",
        xlabel="Date",
        ylabel="Rentals"
    )

    ax.legend()

    charts["08_daily_demand.jpg"] = save_jpg(fig)

    # I. Holiday demand
    holidays = (
        active.groupby("is_holiday")[
            "rented_bike_count"
        ]
        .mean()
        .reindex([False, True])
    )

    fig, ax = plt.subplots(figsize=(7.5, 4.8))

    ax.bar(
        ["No holiday", "Holiday"],
        holidays.values
    )

    ax.set(
        title="Average rentals by holiday status",
        xlabel="Holiday status",
        ylabel="Average rentals"
    )

    charts["09_holiday_demand.jpg"] = save_jpg(fig)

    return charts, hourly, seasons, correlation


def build_outputs(
    parquet_bytes,
    transform_report,
    curated_key
):
    source_hash = check_lineage(
        parquet_bytes,
        transform_report,
        curated_key
    )

    df, active = prepare_dataframe(
        parquet_bytes
    )

    charts, hourly, seasons, correlation = make_plots(
        df,
        active
    )

    summary = {
        "project": "bikepulse",
        "analysis_version": "k001",
        "dataset_version": "dv001",
        "generated_at_utc": utc_now(),
        "input_parquet_key": curated_key,
        "input_parquet_sha256": source_hash,
        "total_rows": int(len(df)),
        "functioning_rows": int(len(active)),
        "non_functioning_rows": int(
            len(df) - len(active)
        ),
        "date_start": (
            df["date"].min().date().isoformat()
        ),
        "date_end": (
            df["date"].max().date().isoformat()
        ),
        "mean_hourly_rentals_functioning": round(
            float(active["rented_bike_count"].mean()),
            2
        ),
        "peak_average_hour": int(
            hourly.idxmax()
        ),
        "peak_average_season": str(
            seasons.idxmax()
        ),
        "temperature_rental_pearson": round(
            float(
                correlation.loc[
                    "temperature_c",
                    "rented_bike_count"
                ]
            ),
            4
        ),
        "rainfall_rental_pearson": round(
            float(
                correlation.loc[
                    "rainfall_mm",
                    "rented_bike_count"
                ]
            ),
            4
        ),
        "interpretation_limitations": [
            "Historical 2017-2018 observations",
            "Correlations do not establish causation",
            "Future weather requires forecast or scenario",
            "Non-functioning hours excluded from demand patterns"
        ]
    }

    feature_recommendations = {
        "analysis_version": "k001",
        "recommendation_type": (
            "EDA hypotheses, not trained feature importance"
        ),
        "calendar_known_ahead": [
            "hour",
            "day_of_week",
            "is_weekend",
            "month",
            "season",
            "is_holiday"
        ],
        "weather_requires_forecast_or_scenario": [
            "temperature_c",
            "humidity_pct",
            "wind_speed_mps",
            "visibility_10m",
            "dew_point_temperature_c",
            "solar_radiation_mj_m2",
            "rainfall_mm",
            "snowfall_cm"
        ],
        "not_in_prediction_features": [
            "rented_bike_count",
            "is_functioning_day"
        ],
        "training_policy": (
            "Filter functioning-day records before training"
        ),
        "validation_policy": (
            "Chronological train/validation/test split; "
            "fit preprocessing using train data only"
        ),
        "warning": (
            "Descriptive patterns do not prove causation "
            "or model feature importance"
        )
    }

    markdown_report = (
        "# SEOUL BIKE PULSE - EDA Report\n\n"
        f"Dataset version: dv001\n\n"
        f"Total rows: {len(df):,}\n\n"
        f"Functioning rows: {len(active):,}\n\n"
        f"Mean rentals per functioning hour: "
        f"{summary['mean_hourly_rentals_functioning']}\n\n"
        f"Highest average hour: "
        f"{summary['peak_average_hour']}:00\n\n"
        f"Highest average season: "
        f"{summary['peak_average_season']}\n\n"
        "Charts: JPG files in charts/.\n\n"
        "Interpretation: Correlations are descriptive, "
        "not causal. Future weather must come from a "
        "forecast or user-supplied scenario.\n"
    )

    outputs = {
        "eda_summary.json": json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        ).encode("utf-8"),
        "feature_recommendations.json": json.dumps(
            feature_recommendations,
            ensure_ascii=False,
            indent=2
        ).encode("utf-8"),
        "EDA_REPORT.md": markdown_report.encode(
            "utf-8"
        )
    }

    for filename, contents in charts.items():
        outputs[
            f"charts/{filename}"
        ] = contents

    return outputs


def run_local(args):
    parquet_bytes = Path(
        args.parquet
    ).read_bytes()

    report = json.loads(
        Path(args.transform_report).read_text(
            encoding="utf-8"
        )
    )

    outputs = build_outputs(
        parquet_bytes,
        report,
        args.curated_key
    )

    target = Path(args.output_dir)

    for filename, contents in outputs.items():
        path = target / filename

        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        path.write_bytes(contents)

        print("SAVED:", path)

    print("EDA_LOCAL_PASSED")


def run_s3():
    import boto3

    bucket = os.environ["BIKEPULSE_BUCKET"]
    curated_key = os.environ["CURATED_KEY"]
    report_key = os.environ["TRANSFORM_REPORT_KEY"]
    run_id = os.environ["EDA_RUN_ID"]

    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
        run_id
    ):
        raise ValueError(
            "INVALID_EDA_RUN_ID"
        )

    expected_key = (
        "curated/dataset=seoul-bike/"
        "version=dv001/bike_data.parquet"
    )

    if curated_key != expected_key:
        raise ValueError(
            "INVALID_CURATED_KEY"
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

    s3 = boto3.client("s3")

    response = s3.get_object(
        Bucket=bucket,
        Key=curated_key
    )

    parquet_bytes = response["Body"].read()

    report = json.loads(
        s3.get_object(
            Bucket=bucket,
            Key=report_key
        )["Body"].read()
    )

    if (
        response.get("Metadata", {}).get(
            "source-sha256"
        )
        != report.get("source_sha256")
    ):
        raise ValueError(
            "PARQUET_SOURCE_LINEAGE_MISMATCH"
        )

    outputs = build_outputs(
        parquet_bytes,
        report,
        curated_key
    )

    prefix = (
        "monitoring/eda-reports/"
        "dataset_version=dv001/"
        f"run_id={run_id}/"
    )

    manifest_items = []

    for filename, contents in outputs.items():
        key = prefix + filename

        if filename.endswith(".jpg"):
            content_type = "image/jpeg"
        elif filename.endswith(".json"):
            content_type = "application/json"
        else:
            content_type = "text/markdown; charset=utf-8"

        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=contents,
            ContentType=content_type,
            IfNoneMatch="*"
        )

        manifest_items.append({
            "key": key,
            "sha256": sha256_bytes(contents),
            "bytes": len(contents)
        })

        print(
            "EDA_ARTIFACT_SAVED:",
            key,
            flush=True
        )

    manifest = {
        "status": "PASSED",
        "analysis_version": "k001",
        "dataset_version": "dv001",
        "run_id": run_id,
        "created_at_utc": utc_now(),
        "input_parquet_key": curated_key,
        "input_parquet_sha256": sha256_bytes(
            parquet_bytes
        ),
        "transformation_report_key": report_key,
        "artifacts": manifest_items
    }

    manifest_key = prefix + "manifest.json"

    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(
            manifest,
            indent=2
        ).encode("utf-8"),
        ContentType="application/json",
        IfNoneMatch="*"
    )

    print(
        "EDA_MANIFEST_SAVED:",
        manifest_key,
        flush=True
    )

    print(
        "EDA_STATUS: PASSED",
        flush=True
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
        default="data/eda/k_local"
    )

    args = parser.parse_args()

    if args.local:
        run_local(args)
    else:
        run_s3()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"EDA_FAILED: {type(exc).__name__}: {exc}",
            flush=True
        )
        sys.exit(1)
