import argparse
import hashlib
import io
import json
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
import xgboost
from sklearn.model_selection import (
 GridSearchCV,
 RandomizedSearchCV,
 TimeSeriesSplit
)
from xgboost import XGBRegressor
from training_worker import (
 load_training_data,
 make_pipeline,
 calculate_metrics,
 json_bytes,
 sha256,
 require,
 SPLIT_PREFIX
)

DATASET_VERSION = "dv001"
FEATURE_VERSION = "fv001"
WORKER_VERSION = "n001"

BASELINE_ROOT = (
 "models/experiments/"
 "feature_version=fv001/"
)

def utc_now():
 return datetime.now(
 timezone.utc
 ).isoformat()

def validate_id(value, label):
 require(
 re.fullmatch(
 r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
 value
 ),
 f"INVALID_{label}"
 )

def baseline_prefix(run_id):
 validate_id(run_id, "BASELINE_RUN_ID")
 return (
 BASELINE_ROOT
 + f"run_id={run_id}/"
 )

def validate_baseline(
 reader,
 baseline_run_id,
 training_data
):
 prefix = baseline_prefix(
 baseline_run_id
 )
 manifest = json.loads(
 reader(
 prefix + "run_manifest.json"
 )
 )
 require(
 manifest.get("status") == "PASSED"
 and manifest.get("worker_version") == "m001"
 and manifest.get("run_id") == baseline_run_id,
 "INVALID_BASELINE_MANIFEST"
 )
 require(
 manifest.get("test_evaluated") is not True,
 "BASELINE_TEST_ALREADY_EVALUATED"
 )
 artifact_index = {
 item["path"]: item
 for item in manifest["artifacts"]
 }
 
 def verified_json(name):
  require(
   name in artifact_index,
   f"BASELINE_ARTIFACT_NOT_LISTED: {name}"
  )
  content = reader(prefix + name)
  require(
   sha256(content)
   == artifact_index[name]["sha256"],
   f"BASELINE_SHA256_MISMATCH: {name}"
  )
  return json.loads(content)

 report = verified_json(
 "training_report.json"
 )
 comparison = verified_json(
 "metrics/comparison.json"
 )
 require(
 report.get("status") == "PASSED"
 and report.get("worker_version") == "m001"
 and report.get("feature_version") == "fv001"
 and report.get("dataset_version") == "dv001"
 and report.get("test_evaluated") is False,
 "INVALID_BASELINE_TRAINING_REPORT"
 )
 require(
 comparison.get("test_evaluated") is False,
 "INVALID_BASELINE_COMPARISON"
 )
 require(
 report.get("feature_report_key")
 == training_data["feature_report_key"],
 "BASELINE_FEATURE_RUN_MISMATCH"
 )
 require(
 report.get("feature_columns")
 == training_data["feature_columns"],
 "BASELINE_FEATURE_COLUMNS_MISMATCH"
 )
 require(
 report.get("train_rows")
 == len(training_data["X_train"])
 and report.get("validation_rows")
 == len(training_data["X_validation"]),
 "BASELINE_DATASET_SIZE_MISMATCH"
 )
 models = comparison["models"]
 require(
 {item["model"] for item in models}
 == {"dummy", "ridge", "random_forest"},
 "BASELINE_MODEL_LIST_MISMATCH"
 )
 print(
 "BASELINE_COMPARISON_VALIDATION: PASSED",
 flush=True
 )
 return models, prefix

def create_date_folds(reader, training_data):
 key = SPLIT_PREFIX + "X_train.parquet"
 full = pd.read_parquet(
 io.BytesIO(reader(key))
 )
 feature_columns = (
 training_data["feature_columns"]
 )
 require(
 full[feature_columns].equals(
 training_data["X_train"]
 ),
 "CV_TRAIN_DATA_MISMATCH"
 )
 timestamps = pd.to_datetime(
 full["timestamp"],
 errors="raise"
 )
 require(
 timestamps.is_monotonic_increasing,
 "CV_TIMESTAMPS_NOT_SORTED"
 )
 dates = timestamps.dt.normalize()
 unique_days = pd.DatetimeIndex(
 dates.unique()
 ).sort_values()
 require(
 len(unique_days) >= 30,
 "INSUFFICIENT_TRAIN_DATES_FOR_CV"
 )
 splitter = TimeSeriesSplit(
 n_splits=3,
 gap=1
 )
 folds = []
 descriptions = []
 for fold_number, (
  train_date_indices,
  validation_date_indices
 ) in enumerate(
  splitter.split(unique_days),
  start=1
 ):
  train_days = unique_days[
   train_date_indices
  ]
  validation_days = unique_days[
   validation_date_indices
  ]
  train_indices = np.flatnonzero(
   dates.isin(train_days).to_numpy()
  )
  validation_indices = np.flatnonzero(
   dates.isin(validation_days).to_numpy()
  )
  require(
   len(train_indices) > 0
   and len(validation_indices) > 0,
   f"EMPTY_CV_FOLD: {fold_number}"
  )
  require(
   train_days.max() < validation_days.min(),
   f"CV_CHRONOLOGY_FAILED: {fold_number}"
  )
  require(
   set(train_indices).isdisjoint(
    set(validation_indices)
   ),
   f"CV_OVERLAP: {fold_number}"
  )
  folds.append((
   train_indices,
   validation_indices
  ))
  descriptions.append({
   "fold": fold_number,
   "train_rows": int(
    len(train_indices)
   ),
   "validation_rows": int(
    len(validation_indices)
   ),
   "train_start": (
    train_days.min().date().isoformat()
   ),
   "train_end": (
    train_days.max().date().isoformat()
   ),
   "validation_start": (
    validation_days.min().date().isoformat()
   ),
   "validation_end": (
    validation_days.max().date().isoformat()
   )
  })
 print(
  "TIME_SERIES_CV_VALIDATION: PASSED",
  flush=True
 )
 for item in descriptions:
  print(
   "CV_FOLD:",
   json.dumps(item),
   flush=True
  )
 return folds, descriptions

def make_xgb_pipeline(feature_columns):
 model = XGBRegressor(
  objective="reg:squarederror",
  eval_metric="mae",
  tree_method="hist",
  n_estimators=180,
  max_depth=4,
  learning_rate=0.08,
  min_child_weight=2,
  subsample=0.90,
  colsample_bytree=0.90,
  reg_lambda=1.0,
  random_state=42,
  n_jobs=1,
  verbosity=0
 )
 return make_pipeline(
  model,
  feature_columns
 )

def make_grid_search(feature_columns, folds):
 param_grid = {
  "model__n_estimators": [
   120, 240
  ],
  "model__max_depth": [
   3, 5
  ],
  "model__learning_rate": [
   0.05, 0.10
  ]
 }
 return GridSearchCV(
  estimator=make_xgb_pipeline(
   feature_columns
  ),
  param_grid=param_grid,
  scoring="neg_mean_absolute_error",
  cv=folds,
  n_jobs=1,
  refit=True,
  verbose=0,
  error_score="raise",
  return_train_score=False
 )

def make_random_search(feature_columns, folds):
 param_distributions = {
  "model__n_estimators": [
   120, 180, 240, 320
  ],
  "model__max_depth": [
   2, 3, 4, 5, 6
  ],
  "model__learning_rate": [
   0.03, 0.05, 0.08, 0.10
  ],
  "model__min_child_weight": [
   1, 3, 5
  ],
  "model__subsample": [
   0.75, 0.90, 1.0
  ],
  "model__colsample_bytree": [
   0.75, 0.90, 1.0
  ]
 }
 return RandomizedSearchCV(
  estimator=make_xgb_pipeline(
   feature_columns
  ),
  param_distributions=param_distributions,
  n_iter=8,
  scoring="neg_mean_absolute_error",
  cv=folds,
  n_jobs=1,
  refit=True,
  random_state=42,
  verbose=0,
  error_score="raise",
  return_train_score=False
 )

def clean_parameters(parameters):
 cleaned = {}
 for name, value in parameters.items():
  short_name = name.removeprefix(
   "model__"
  )
  if hasattr(value, "item"):
   value = value.item()
  cleaned[short_name] = value
 return cleaned

def search_report(search, name):
 results = pd.DataFrame(
  search.cv_results_
 )
 results = results.sort_values(
  [
   "rank_test_score",
   "mean_fit_time"
  ]
 )
 candidates = []
 for _, row in results.iterrows():
  candidates.append({
   "rank": int(
    row["rank_test_score"]
   ),
   "params": clean_parameters(
    row["params"]
   ),
   "cv_mean_mae": round(
    -float(
     row["mean_test_score"]
    ),
    6
   ),
   "cv_std_mae": round(
    float(
     row["std_test_score"]
    ),
    6
   ),
   "mean_fit_seconds": round(
    float(
     row["mean_fit_time"]
    ),
    4
   )
  })
 return {
  "search": name,
  "scoring": "negative_mean_absolute_error",
  "selection_metric": "cv_mean_mae",
  "candidate_count": len(candidates),
  "fold_count": 3,
  "best_cv_mae": round(
   -float(
    search.best_score_
   ),
   6
  ),
  "best_params": clean_parameters(
   search.best_params_
  ),
  "candidates": candidates
 }

def evaluate_pipeline(
 name,
 pipeline,
 data,
 fit_seconds,
 cv_summary=None
):
 train_prediction = pipeline.predict(
  data["X_train"]
 )
 validation_prediction = pipeline.predict(
  data["X_validation"]
 )
 train_metrics = calculate_metrics(
  data["y_train"],
  train_prediction
 )
 validation_metrics = calculate_metrics(
  data["y_validation"],
  validation_prediction
 )
 report = {
  "model_name": name,
  "worker_version": WORKER_VERSION,
  "train_rows": len(data["X_train"]),
  "validation_rows": len(
   data["X_validation"]
  ),
  "training_or_search_seconds": round(
   fit_seconds,
   4
  ),
  "train": train_metrics,
  "validation": validation_metrics,
  "cv_best_mae": (
   cv_summary["best_cv_mae"]
   if cv_summary else None
  ),
  "hyperparameters": {
   key: value
   for key, value in (
    pipeline.named_steps["model"]
    .get_params()
    .items()
   )
   if key in [
    "n_estimators",
    "max_depth",
    "learning_rate",
    "min_child_weight",
    "subsample",
    "colsample_bytree",
    "reg_lambda",
    "random_state",
    "n_jobs",
    "tree_method"
   ]
  },
  "test_evaluated": False
 }
 print(
  f"MODEL_FINISHED: {name} "
  f"TRAIN_MAE={train_metrics['mae']} "
  f"VALIDATION_MAE={validation_metrics['mae']}",
  flush=True
 )
 return report, validation_prediction

def build_experiment(
 reader,
 feature_report_key,
 baseline_run_id,
 run_id
):
 data = load_training_data(
  reader,
  feature_report_key
 )
 baseline_models, baseline_location = (
  validate_baseline(
   reader,
   baseline_run_id,
   data
  )
 )
 folds, fold_descriptions = create_date_folds(
  reader,
  data
 )
 X_train = data["X_train"]
 y_train = data["y_train"]
 artifacts = {}
 model_reports = {}
 validation_predictions = (
  data["validation_metadata"].copy()
 )
 validation_predictions["actual_rentals"] = (
  data["y_validation"].to_numpy()
 )

 print(
  "XGB_DEFAULT_START",
  flush=True
 )
 default_pipeline = make_xgb_pipeline(
  data["feature_columns"]
 )
 started = time.perf_counter()
 default_pipeline.fit(
  X_train,
  y_train
 )
 default_seconds = (
  time.perf_counter() - started
 )
 default_report, default_prediction = (
  evaluate_pipeline(
   "xgb_default",
   default_pipeline,
   data,
   default_seconds
  )
 )
 trained = {
  "xgb_default": default_pipeline
 }
 model_reports["xgb_default"] = (
  default_report
 )
 validation_predictions[
  "prediction_xgb_default"
 ] = default_prediction

 print(
  "GRID_SEARCH_START: 8 candidates x 3 folds",
  flush=True
 )
 grid = make_grid_search(
  data["feature_columns"],
  folds
 )
 started = time.perf_counter()
 grid.fit(
  X_train,
  y_train
 )
 grid_seconds = (
  time.perf_counter() - started
 )
 grid_summary = search_report(
  grid,
  "grid"
 )
 grid_pipeline = grid.best_estimator_
 grid_report, grid_prediction = (
  evaluate_pipeline(
   "xgb_grid",
   grid_pipeline,
   data,
   grid_seconds,
   grid_summary
  )
 )
 trained["xgb_grid"] = grid_pipeline
 model_reports["xgb_grid"] = grid_report
 validation_predictions[
  "prediction_xgb_grid"
 ] = grid_prediction

 print(
  "RANDOM_SEARCH_START: 8 candidates x 3 folds",
  flush=True
 )
 randomized = make_random_search(
  data["feature_columns"],
  folds
 )
 started = time.perf_counter()
 randomized.fit(
  X_train,
  y_train
 )
 random_seconds = (
  time.perf_counter() - started
 )
 random_summary = search_report(
  randomized,
  "randomized"
 )
 random_pipeline = (
  randomized.best_estimator_
 )
 random_report, random_prediction = (
  evaluate_pipeline(
   "xgb_random",
   random_pipeline,
   data,
   random_seconds,
   random_summary
  )
 )
 trained["xgb_random"] = random_pipeline
 model_reports["xgb_random"] = random_report
 validation_predictions[
  "prediction_xgb_random"
 ] = random_prediction

 for name, pipeline in trained.items():
  buffer = io.BytesIO()
  joblib.dump(
   pipeline,
   buffer,
   compress=3
  )
  artifacts[
   f"pipelines/{name}.joblib"
  ] = buffer.getvalue()
  artifacts[
   f"metrics/{name}.json"
  ] = json_bytes(
   model_reports[name]
  )
 artifacts[
  "searches/grid_cv.json"
 ] = json_bytes(
  grid_summary
 )
 artifacts[
  "searches/random_cv.json"
 ] = json_bytes(
  random_summary
 )
 artifacts[
  "searches/cv_folds.json"
 ] = json_bytes({
  "method": "TimeSeriesSplit",
  "split_unit": "functioning_calendar_dates",
  "n_splits": 3,
  "gap": 1,
  "folds": fold_descriptions
 })
 predictions_buffer = io.BytesIO()
 validation_predictions.to_parquet(
  predictions_buffer,
  index=False,
  engine="pyarrow",
  compression="snappy"
 )
 artifacts[
  "predictions/validation_predictions.parquet"
 ] = predictions_buffer.getvalue()

 candidates = []
 for item in baseline_models:
  candidates.append({
   "stage": "M",
   "model": item["model"],
   "train_mae": item["train_mae"],
   "validation_mae": item["validation_mae"],
   "validation_rmse": item["validation_rmse"],
   "validation_r2": item["validation_r2"]
  })
 for name, report in model_reports.items():
  candidates.append({
   "stage": "N",
   "model": name,
   "train_mae": (
    report["train"]["mae"]
   ),
   "validation_mae": (
    report["validation"]["mae"]
   ),
   "validation_rmse": (
    report["validation"]["rmse"]
   ),
   "validation_r2": (
    report["validation"]["r2"]
   )
  })
 candidates.sort(
  key=lambda item: (
   item["validation_mae"],
   item["validation_rmse"]
  )
 )
 best_xgb = min(
  model_reports,
  key=lambda name: (
   model_reports[name]["validation"]["mae"],
   model_reports[name]["validation"]["rmse"]
  )
 )
 best_baseline_mae = min(
  item["validation_mae"]
  for item in baseline_models
 )
 best_xgb_mae = (
  model_reports[best_xgb]
  ["validation"]["mae"]
 )
 improvement = (
  (best_baseline_mae - best_xgb_mae)
  / best_baseline_mae * 100
  if best_baseline_mae > 0
  else None
 )
 combined_comparison = {
  "status": "PASSED",
  "dataset_version": DATASET_VERSION,
  "feature_version": FEATURE_VERSION,
  "baseline_run_id": baseline_run_id,
  "selection_metric": "validation_mae",
  "tie_break_metric": "validation_rmse",
  "best_xgboost_candidate": best_xgb,
  "provisional_overall_leader": {
   "stage": candidates[0]["stage"],
   "model": candidates[0]["model"]
  },
  "best_baseline_mae": best_baseline_mae,
  "best_xgboost_mae": best_xgb_mae,
  "xgboost_improvement_percent": (
   round(improvement, 4)
   if improvement is not None else None
  ),
  "test_evaluated": False,
  "models": candidates
 }
 artifacts[
  "comparison/combined_comparison.json"
 ] = json_bytes(
  combined_comparison
 )
 training_report = {
  "status": "PASSED",
  "worker_version": WORKER_VERSION,
  "run_id": run_id,
  "created_at_utc": utc_now(),
  "dataset_version": DATASET_VERSION,
  "feature_version": FEATURE_VERSION,
  "feature_report_key": feature_report_key,
  "baseline_run_id": baseline_run_id,
  "baseline_prefix": baseline_location,
  "train_rows": len(X_train),
  "validation_rows": len(
   data["X_validation"]
  ),
  "feature_count": len(
   data["feature_columns"]
  ),
  "feature_columns": (
   data["feature_columns"]
  ),
  "cv_folds": 3,
  "grid_candidates": (
   grid_summary["candidate_count"]
  ),
  "random_candidates": (
   random_summary["candidate_count"]
  ),
  "best_grid_params": (
   grid_summary["best_params"]
  ),
  "best_random_params": (
   random_summary["best_params"]
  ),
  "best_xgboost_candidate": best_xgb,
  "provisional_overall_leader": (
   combined_comparison[
    "provisional_overall_leader"
   ]
  ),
  "test_evaluated": False,
  "final_model_selection_completed": False,
  "python_version": platform.python_version(),
  "sklearn_version": sklearn.__version__,
  "xgboost_version": xgboost.__version__,
  "pandas_version": pd.__version__,
  "numpy_version": np.__version__,
  "joblib_version": joblib.__version__
 }
 artifacts[
  "training_report.json"
 ] = json_bytes(
  training_report
 )
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
    "path": name,
    "sha256": sha256(content),
    "size_bytes": len(content)
   }
   for name, content in artifacts.items()
  ]
 }
 artifacts[
  "run_manifest.json"
 ] = json_bytes(
  manifest
 )
 return artifacts

def local_mode(args):
 root = Path(args.input_dir)
 def reader(key):
  return (
   root / key
  ).read_bytes()
 artifacts = build_experiment(
  reader,
  args.feature_report_key,
  args.baseline_run_id,
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
   "LOCAL_XGBOOST_ARTIFACT:",
   destination,
   flush=True
  )
 print(
  "LOCAL_XGBOOST_STATUS: PASSED",
  flush=True
 )
 return 0

def s3_mode():
 bucket = os.environ["BIKEPULSE_BUCKET"]
 feature_report_key = (
  os.environ["FEATURE_REPORT_KEY"]
 )
 baseline_run_id = (
  os.environ["BASELINE_RUN_ID"]
 )
 run_id = os.environ["XGB_RUN_ID"]
 validate_id(
  run_id,
  "XGB_RUN_ID"
 )
 validate_id(
  baseline_run_id,
  "BASELINE_RUN_ID"
 )
 require(
  feature_report_key.startswith(
   "curated/_feature_reports/"
   "version=dv001/run_id="
  )
  and feature_report_key.endswith(
   "/feature_report.json"
  ),
  "INVALID_FEATURE_REPORT_KEY"
 )
 prefix = (
  BASELINE_ROOT
  + f"run_id={run_id}/"
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
   baseline_run_id,
   run_id
  )
  for name, content in artifacts.items():
   key = prefix + name
   if name.endswith(".json"):
    content_type = "application/json"
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
    f"UPLOADED_SHA256_MISMATCH: {name}"
   )
   print(
    "XGBOOST_ARTIFACT_SAVED:",
    key,
    flush=True
   )
  print(
   "XGBOOST_STATUS: PASSED",
   flush=True
  )
  return 0
 except Exception as exc:
  message = (
   f"{type(exc).__name__}: {exc}"
  )
  print(
   "XGBOOST_ERROR:",
   message,
   flush=True
  )
  failure = {
   "status": "FAILED",
   "worker_version": WORKER_VERSION,
   "run_id": run_id,
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
    "XGBOOST_FAILURE_REPORT_SAVED",
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
  default="data/xgboost/input"
 )
 parser.add_argument(
  "--output-dir",
  default="data/xgboost/output"
 )
 parser.add_argument(
  "--feature-report-key",
  default=None
 )
 parser.add_argument(
  "--baseline-run-id",
  default=None
 )
 parser.add_argument(
  "--run-id",
  default="n-local-001"
 )
 args = parser.parse_args()
 if args.local:
  require(
   args.feature_report_key is not None
   and args.baseline_run_id is not None,
   "LOCAL_ARGUMENTS_REQUIRED"
  )
  return local_mode(args)
 return s3_mode()

if __name__ == "__main__":
 try:
  sys.exit(main())
 except Exception as exc:
  print(
   "XGBOOST_FATAL_ERROR:",
   f"{type(exc).__name__}: {exc}",
   flush=True
  )
  sys.exit(1)
