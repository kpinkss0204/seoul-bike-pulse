import hashlib
import io
import json
import math
import os
import platform
from dataclasses import dataclass
from importlib.metadata import version as package_version
import boto3
import joblib
import numpy as np
import pandas as pd
import sklearn
import scipy
from jsonschema import Draft202012Validator
from sklearn.pipeline import Pipeline

EXPECTED_FILES = {
 "model.joblib",
 "metadata.json",
 "evaluation.json",
 "input_schema.json",
 "model_card.md",
 "examples/smoke_input.json",
 "examples/smoke_expected.json",
 "environment/requirements.txt",
 "environment/pip_freeze.txt"
}

def check(condition, message):
 if not condition:
  raise ValueError(message)

def sha256(content):
 return hashlib.sha256(
  content
 ).hexdigest()

def read_object(s3, bucket, key, version_id=None):
 params = {
  "Bucket": bucket,
  "Key": key
 }
 if version_id:
  params["VersionId"] = version_id
 return s3.get_object(
  **params
 )["Body"].read()

def validate_feature_payload(schema, features):
 if not isinstance(features, dict):
  return [
   "features must be a JSON object"
  ]
 errors = []
 validator = Draft202012Validator(schema)
 for error in validator.iter_errors(features):
  errors.append(error.message)
 # NaN and Infinity must never reach model inference.
 for name, value in features.items():
  if (
   isinstance(value, (int, float))
   and not isinstance(value, bool)
   and not math.isfinite(float(value))
  ):
   errors.append(
    f"{name}: finite number required"
   )
 return errors

def check_runtime_versions(metadata):
 expected = metadata["environment"]
 actual = {
  "sklearn": sklearn.__version__,
  "numpy": np.__version__,
  "pandas": pd.__version__,
  "joblib": joblib.__version__,
  "scipy": scipy.__version__
 }
 for name, current in actual.items():
  check(
   str(expected[name]) == str(current),
   "RUNTIME_VERSION_MISMATCH: "
   + name
   + f" expected={expected[name]}"
   + f" actual={current}"
  )
 python_expected = ".".join(
  expected["python"].split(".")[:2]
 )
 python_actual = ".".join(
  platform.python_version().split(".")[:2]
 )
 check(
  python_expected == python_actual,
  "PYTHON_VERSION_MISMATCH"
 )
 if metadata["training_stage"] == "N":
  import xgboost
  check(
   expected["xgboost"]
   == xgboost.__version__,
   "XGBOOST_VERSION_MISMATCH"
  )

@dataclass
class ModelRuntime:
 model: object
 metadata: dict
 schema: dict
 smoke_input: dict
 model_sha256: str

def predict_raw(runtime, features):
 columns = runtime.metadata[
  "feature_columns"
 ]
 ordered = {
  name: features[name]
  for name in columns
 }
 frame = pd.DataFrame(
  [ordered],
  columns=columns
 )
 value = float(
  runtime.model.predict(
   frame
  )[0]
 )
 check(
  math.isfinite(value),
  "NON_FINITE_MODEL_PREDICTION"
 )
 return value

def load_model_runtime():
 model_id = os.environ[
  "MODEL_ID"
 ]
 model_version = os.environ[
  "MODEL_VERSION"
 ]
 registry_name = os.environ[
  "REGISTRY_TABLE"
 ]
 expected_bucket = os.environ[
  "BIKEPULSE_BUCKET"
 ]
 session = boto3.Session(
  region_name=os.environ.get(
   "AWS_REGION",
   "ap-northeast-2"
  )
 )
 table = session.resource(
  "dynamodb"
 ).Table(registry_name)
 s3 = session.client("s3")

 # A. Check staging alias.
 alias = table.get_item(
  Key={
   "model_id": model_id,
   "model_version": "ALIAS#STAGING"
  },
  ConsistentRead=True
 ).get("Item")
 check(
  alias is not None
  and alias.get("target_version")
  == model_version,
  "STAGING_ALIAS_MISMATCH"
 )

 # B. Read exact registered model version.
 record = table.get_item(
  Key={
   "model_id": model_id,
   "model_version": model_version
  },
  ConsistentRead=True
 ).get("Item")
 check(
  record is not None,
  "MODEL_NOT_REGISTERED"
 )
 check(
  record.get("item_type") == "MODEL"
  and record.get("status") == "STAGING",
  "MODEL_NOT_STAGING"
 )
 check(
  record.get("feature_version") == "fv001"
  and int(record.get("feature_count", 0)) == 19,
  "REGISTRY_FEATURE_CONTRACT_MISMATCH"
 )
 check(
  record["model_s3_bucket"] == expected_bucket,
  "UNEXPECTED_MODEL_BUCKET"
 )
 prefix = (
  "models/registry-candidates/"
  f"model_id={model_id}/"
  f"version={model_version}/"
 )
 check(
  record["package_prefix"] == prefix
  and record["model_s3_key"]
  == prefix + "model.joblib"
  and record["package_manifest_key"]
  == prefix + "package_manifest.json",
  "REGISTRY_PACKAGE_PATH_MISMATCH"
 )
 bucket = record[
  "model_s3_bucket"
 ]

 # C. Verify package manifest.
 manifest_bytes = read_object(
  s3,
  bucket,
  record["package_manifest_key"],
  record.get(
   "package_manifest_s3_version_id"
  )
 )
 check(
  sha256(manifest_bytes)
  == record["package_manifest_sha256"],
  "MANIFEST_SHA256_MISMATCH"
 )
 manifest = json.loads(
  manifest_bytes
 )
 check(
  manifest["status"] == "PASSED"
  and manifest["model_id"] == model_id
  and manifest["model_version"] == model_version,
  "INVALID_PACKAGE_MANIFEST"
 )
 index = {
  item["path"]: item
  for item in manifest["files"]
 }
 check(
  set(index) == EXPECTED_FILES
  and len(manifest["files"]) == len(EXPECTED_FILES),
  "PACKAGE_FILE_SET_MISMATCH"
 )

 # D. Verify all nine package files.
 verified = {}
 for name, item in index.items():
  version_id = (
   record.get("model_s3_version_id")
   if name == "model.joblib"
   else None
  )
  content = read_object(
   s3,
   bucket,
   prefix + name,
   version_id
  )
  check(
   sha256(content) == item["sha256"],
   "PACKAGE_ARTIFACT_SHA256_MISMATCH: "
   + name
  )
  check(
   len(content) == item["size_bytes"],
   "PACKAGE_ARTIFACT_SIZE_MISMATCH: "
   + name
  )
  verified[name] = content

 model_sha = sha256(
  verified["model.joblib"]
 )
 check(
  model_sha == record["model_sha256"]
  and model_sha == manifest["model_sha256"],
  "MODEL_SHA256_MISMATCH"
 )

 metadata = json.loads(
  verified["metadata.json"]
 )
 schema = json.loads(
  verified["input_schema.json"]
 )
 evaluation = json.loads(
  verified["evaluation.json"]
 )
 smoke = json.loads(
  verified["examples/smoke_input.json"]
 )
 expected_smoke = json.loads(
  verified["examples/smoke_expected.json"]
 )

 # E. Check model lineage and feature contract.
 check(
  metadata["model_id"] == model_id
  and metadata["model_version"] == model_version
  and metadata["model_sha256"] == model_sha,
  "MODEL_METADATA_MISMATCH"
 )
 check(
  metadata["selection_id"]
  == evaluation["selection_id"]
  and metadata["evaluation_run_id"]
  == evaluation["evaluation_run_id"]
  and evaluation["model_sha256"] == model_sha
  and evaluation["status"] == "PASSED",
  "EVALUATION_LINEAGE_MISMATCH"
 )
 columns = metadata[
  "feature_columns"
 ]
 check(
  len(columns) == 19
  and len(set(columns)) == 19
  and schema["required"] == columns
  and set(schema["properties"]) == set(columns)
  and schema["additionalProperties"] is False,
  "INPUT_SCHEMA_MISMATCH"
 )
 check(
  record["input_schema_sha256"]
  == index["input_schema.json"]["sha256"],
  "INPUT_SCHEMA_SHA256_MISMATCH"
 )
 Draft202012Validator.check_schema(
  schema
 )
 check_runtime_versions(
  metadata
 )

 # F. Load only a trusted, hash-verified Joblib.
 model = joblib.load(
  io.BytesIO(
   verified["model.joblib"]
  )
 )
 check(
  isinstance(model, Pipeline),
  "INVALID_MODEL_PIPELINE"
 )
 check(
  list(model.feature_names_in_) == columns,
  "PIPELINE_FEATURE_ORDER_MISMATCH"
 )
 runtime = ModelRuntime(
  model=model,
  metadata=metadata,
  schema=schema,
  smoke_input=smoke,
  model_sha256=model_sha
 )

 # G. Reproduce original validation prediction.
 sample = smoke["features"]
 errors = validate_feature_payload(
  schema,
  sample
 )
 check(
  not errors,
  "SMOKE_INPUT_SCHEMA_FAILED: "
  + str(errors)
 )
 observed = predict_raw(
  runtime,
  sample
 )
 expected_value = float(
  expected_smoke["expected_prediction"]
 )
 check(
  np.isclose(
   observed,
   expected_value,
   rtol=float(expected_smoke["rtol"]),
   atol=float(expected_smoke["atol"])
  ),
  "MODEL_SMOKE_PREDICTION_MISMATCH"
 )
 print(
  json.dumps({
   "event": "MODEL_LOADED",
   "model_id": model_id,
   "model_version": model_version,
   "model_name": metadata["model_name"],
   "model_sha256": model_sha,
   "feature_count": len(columns),
   "smoke_test": "PASSED"
  }),
  flush=True
 )
 return runtime
