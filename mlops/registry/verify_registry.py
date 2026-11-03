import hashlib
import json
import os
from decimal import Decimal
import boto3

BUCKET = os.environ["BIKEPULSE_BUCKET"]
TABLE = os.environ["REGISTRY_TABLE"]
MODEL_ID = "bike-demand"
MODEL_VERSION = "mv001"

def sha256(content):
 return hashlib.sha256(
 content
 ).hexdigest()

def main():
 session = boto3.Session(
 region_name=os.environ.get(
 "AWS_REGION",
 "ap-northeast-2"
 )
 )
 table = session.resource(
 "dynamodb"
 ).Table(TABLE)
 s3 = session.client("s3")

 response = table.get_item(
 Key={
 "model_id": MODEL_ID,
 "model_version": MODEL_VERSION
 },
 ConsistentRead=True
 )
 assert "Item" in response
 item = response["Item"]

 assert item["item_type"] == "MODEL"
 assert item["status"] == "STAGING"
 assert item["approval_status"] == "PENDING"
 assert item["deployment_status"] == (
 "NOT_DEPLOYED"
 )
 assert item["feature_version"] == "fv001"
 assert item["dataset_version"] == "dv001"
 assert item["feature_count"] == 19

 alias_response = table.get_item(
 Key={
 "model_id": MODEL_ID,
 "model_version": "ALIAS#STAGING"
 },
 ConsistentRead=True
 )
 assert "Item" in alias_response
 alias = alias_response["Item"]
 assert alias["item_type"] == "ALIAS"
 assert alias["target_version"] == (
 MODEL_VERSION
 )

 model_bytes = s3.get_object(
 Bucket=BUCKET,
 Key=item["model_s3_key"]
 )["Body"].read()
 actual_model_sha = sha256(
 model_bytes
 )
 assert actual_model_sha == (
 item["model_sha256"]
 )

 manifest_bytes = s3.get_object(
 Bucket=BUCKET,
 Key=item["package_manifest_key"]
 )["Body"].read()
 assert sha256(manifest_bytes) == (
 item["package_manifest_sha256"]
 )

 manifest = json.loads(
 manifest_bytes
 )
 assert manifest["status"] == "PASSED"
 assert manifest["model_id"] == MODEL_ID
 assert manifest["model_version"] == (
 MODEL_VERSION
 )
 assert manifest["model_sha256"] == (
 actual_model_sha
 )

 metadata = json.loads(
 s3.get_object(
 Bucket=BUCKET,
 Key=item["package_prefix"] + "metadata.json"
 )["Body"].read()
 )
 assert metadata["model_sha256"] == (
 item["model_sha256"]
 )
 assert Decimal(str(
 metadata["test_metrics"]["mae"]
 )) == item["test_mae"]
 assert Decimal(str(
 metadata["test_metrics"]["rmse"]
 )) == item["test_rmse"]
 assert Decimal(str(
 metadata["test_metrics"]["r2"]
 )) == item["test_r2"]
 assert metadata["selection_id"] == (
 item["selection_id"]
 )
 assert metadata["evaluation_run_id"] == (
 item["evaluation_run_id"]
 )

 print("MODEL ITEM: PASSED")
 print("STAGING ALIAS: PASSED")
 print("S3 MODEL SHA256: PASSED")
 print("PACKAGE MANIFEST: PASSED")
 print("TEST METRICS: PASSED")
 print("MODEL LINEAGE: PASSED")
 print()
 print("MODEL REGISTRY INTEGRATION: PASSED")

if __name__ == "__main__":
 main()
