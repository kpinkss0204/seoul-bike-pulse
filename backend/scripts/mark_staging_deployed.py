import json
import os
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.types import TypeSerializer

MODEL_ID = "bike-demand"
VERSION = os.environ["MODEL_VERSION"]
TABLE_NAME = os.environ["REGISTRY_TABLE"]
MODEL_SHA = os.environ["DEPLOYED_MODEL_SHA"]
TASK_DEF = os.environ["API_TASK_DEF_ARN"]
IMAGE_DIGEST = os.environ["API_IMAGE_DIGEST"]

serializer = TypeSerializer()

def ddb_map(data):
 return {
  name: serializer.serialize(value)
  for name, value in data.items()
 }

client = boto3.client("dynamodb")
now = datetime.now(
 timezone.utc
).isoformat()

client.transact_write_items(
 TransactItems=[
  {
   "ConditionCheck": {
    "TableName": TABLE_NAME,
    "Key": ddb_map({
     "model_id": MODEL_ID,
     "model_version": "ALIAS#STAGING"
    }),
    "ConditionExpression": (
     "#target = :version "
     "AND #type = :alias"
    ),
    "ExpressionAttributeNames": {
     "#target": "target_version",
     "#type": "item_type"
    },
    "ExpressionAttributeValues": ddb_map({
     ":version": VERSION,
     ":alias": "ALIAS"
    })
   }
  },
  {
   "Update": {
    "TableName": TABLE_NAME,
    "Key": ddb_map({
     "model_id": MODEL_ID,
     "model_version": VERSION
    }),
    "UpdateExpression": (
     "SET #deployment = :deployed, "
     "staging_task_definition = :task, "
     "staging_image_digest = :digest, "
     "staging_deployed_at = :now, "
     "updated_at = :now"
    ),
    "ConditionExpression": (
     "#status = :staging "
     "AND approval_status = :pending "
     "AND model_sha256 = :sha "
     "AND #deployment IN "
     "(:not_deployed, :already_deployed)"
    ),
    "ExpressionAttributeNames": {
     "#deployment": "deployment_status",
     "#status": "status"
    },
    "ExpressionAttributeValues": ddb_map({
     ":deployed": "STAGING_DEPLOYED",
     ":not_deployed": "NOT_DEPLOYED",
     ":already_deployed": "STAGING_DEPLOYED",
     ":staging": "STAGING",
     ":pending": "PENDING",
     ":sha": MODEL_SHA,
     ":task": TASK_DEF,
     ":digest": IMAGE_DIGEST,
     ":now": now
    })
   }
  }
 ]
)

print(
 json.dumps({
  "status": "PASSED",
  "model_id": MODEL_ID,
  "model_version": VERSION,
  "deployment_status": "STAGING_DEPLOYED",
  "task_definition": TASK_DEF,
  "image_digest": IMAGE_DIGEST
 }, indent=2)
)
