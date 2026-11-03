import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from app.model_runtime import (
 load_model_runtime,
 predict_raw,
 validate_feature_payload
)

logging.basicConfig(
 level=logging.INFO,
 format="%(message)s"
)
logger = logging.getLogger(
 "bikepulse-api"
)

class PredictionRequest(BaseModel):
 model_config = ConfigDict(
  extra="forbid"
 )
 features: dict[str, Any]

@asynccontextmanager
async def lifespan(app: FastAPI):
 # Startup must fail if model integrity checks fail.
 app.state.runtime = load_model_runtime()
 logger.info(
  json.dumps({
   "event": "API_READY",
   "model_version": (
    app.state.runtime.metadata[
     "model_version"
    ]
   )
  })
 )
 yield
 logger.info(
  json.dumps({
   "event": "API_SHUTDOWN"
  })
 )

app = FastAPI(
 title="SEOUL BIKE PULSE API",
 version="1.0.0",
 lifespan=lifespan,
 docs_url=None,
 redoc_url=None,
 openapi_url=None
)

def runtime_of(request: Request):
 return request.app.state.runtime

@app.get("/")
def root():
 return {
  "service": "bikepulse-api",
  "api_version": "v1"
 }

@app.get("/api/v1/health")
def health(request: Request):
 runtime = runtime_of(
  request
 )
 return {
  "status": "ok",
  "model_loaded": True,
  "model_id": runtime.metadata[
   "model_id"
  ],
  "model_version": runtime.metadata[
   "model_version"
  ]
 }

@app.get("/api/v1/model")
def model_info(request: Request):
 runtime = runtime_of(
  request
 )
 meta = runtime.metadata
 return {
  "model_id": meta["model_id"],
  "model_version": meta["model_version"],
  "model_name": meta["model_name"],
  "feature_version": meta["feature_version"],
  "feature_count": meta["feature_count"],
  "model_sha256": runtime.model_sha256,
  "environment": "STAGING",
  "frontend_8_field_supported": False
 }

@app.get("/api/v1/model/schema")
def model_schema(request: Request):
 return runtime_of(
  request
 ).schema

@app.get("/api/v1/model/example")
def model_example(request: Request):
 enabled = os.environ.get(
  "ENABLE_DEMO_ENDPOINTS",
  "false"
 ).lower() == "true"
 if not enabled:
  raise HTTPException(
   status_code=404,
   detail="Demo endpoint disabled"
  )
 runtime = runtime_of(
  request
 )
 return {
  "features": runtime.smoke_input[
   "features"
  ]
 }

@app.post("/api/v1/predict")
def predict(
 body: PredictionRequest,
 request: Request
):
 started = time.perf_counter()
 request_id = str(uuid.uuid4())
 runtime = runtime_of(
  request
 )
 errors = validate_feature_payload(
  runtime.schema,
  body.features
 )
 if errors:
  logger.warning(
   json.dumps({
    "event": "PREDICTION_REJECTED",
    "request_id": request_id,
    "model_version": (
     runtime.metadata["model_version"]
    ),
    "error_count": len(errors)
   })
  )
  raise HTTPException(
   status_code=422,
   detail={
    "code": "INVALID_FEATURES",
    "errors": errors[:25],
    "expected_feature_count": 19
   }
  )
 try:
  value = predict_raw(
   runtime,
   body.features
  )
 except Exception:
  logger.exception(
   json.dumps({
    "event": "PREDICTION_FAILED",
    "request_id": request_id
   })
  )
  raise HTTPException(
   status_code=500,
   detail={
    "code": "PREDICTION_FAILED",
    "request_id": request_id
   }
  )
 if not math.isfinite(value):
  raise HTTPException(
   status_code=500,
   detail="Non-finite model output"
  )
 elapsed_ms = round(
  (
   time.perf_counter()
   - started
  ) * 1000,
  3
 )
 logger.info(
  json.dumps({
   "event": "PREDICTION_SUCCEEDED",
   "request_id": request_id,
   "model_version": (
    runtime.metadata["model_version"]
   ),
   "latency_ms": elapsed_ms
  })
 )
 return {
  "model_id": runtime.metadata["model_id"],
  "model_version": (
   runtime.metadata["model_version"]
  ),
  "feature_version": (
   runtime.metadata["feature_version"]
  ),
  "prediction_raw": value,
  "predicted_rentals_display": max(
   0,
   round(value)
  ),
  "request_id": request_id,
  "latency_ms": elapsed_ms
 }
