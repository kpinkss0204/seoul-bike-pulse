import json
import os
from pathlib import Path
import boto3

ecs = boto3.client("ecs")
cluster = ecs.describe_clusters(
 clusters=[
 os.environ["ECS_CLUSTER"]
 ]
)["clusters"][0]
assert cluster["status"] == "ACTIVE"

definition = {
 "Comment": "BikePulse S3 EventBridge ingestion validation",
 "StartAt": "CheckS3Event",
 "TimeoutSeconds": 1200,
 "States": {
 "CheckS3Event": {
 "Type": "Choice",
 "Choices": [
 {
 "And": [
 {
 "Variable": "$.source",
 "StringEquals": "aws.s3"
 },
 {
 "Variable": "$.detail-type",
 "StringEquals": "Object Created"
 },
 {
 "Variable": "$.detail.object.key",
 "IsPresent": True
 }
 ],
 "Next": "RunIngestionWorker"
 }
 ],
 "Default": "IngestionFailed"
 },
 "RunIngestionWorker": {
 "Type": "Task",
 "Resource": "arn:aws:states:::ecs:runTask.sync",
 "Parameters": {
 "Cluster": cluster["clusterArn"],
 "TaskDefinition": os.environ["INGESTION_TASK_DEF_ARN"],
 "LaunchType": "FARGATE",
 "PlatformVersion": "LATEST",
 "NetworkConfiguration": {
 "AwsvpcConfiguration": {
 "Subnets": [os.environ["PRIVATE_A"]],
 "SecurityGroups": [os.environ["SG_MLOPS"]],
 "AssignPublicIp": "DISABLED"
 }
 },
 "Overrides": {
 "ContainerOverrides": [
 {
 "Name": "ingestion",
 "Environment": [
 {
 "Name": "S3_EVENT_JSON",
 "Value.$": "States.JsonToString($)"
 }
 ]
 }
 ]
 }
 },
 "TimeoutSeconds": 900,
 "ResultPath": "$.ecsResult",
 "Retry": [
 {
 "ErrorEquals": ["AmazonECS.Unknown"],
 "IntervalSeconds": 10,
 "MaxAttempts": 2,
 "BackoffRate": 2
 }
 ],
 "Catch": [
 {
 "ErrorEquals": ["States.ALL"],
 "ResultPath": "$.taskError",
 "Next": "IngestionFailed"
 }
 ],
 "Next": "CheckWorkerExitCode"
 },
 "CheckWorkerExitCode": {
 "Type": "Choice",
 "Choices": [
 {
 "Variable": "$.ecsResult.Containers[0].ExitCode",
 "NumericEquals": 0,
 "Next": "IngestionCompleted"
 }
 ],
 "Default": "IngestionFailed"
 },
 "IngestionCompleted": {
 "Type": "Succeed"
 },
 "IngestionFailed": {
 "Type": "Fail",
 "Error": "BikePulseIngestionFailed",
 "Cause": "Review DynamoDB ingestion ledger, S3 quality report, and ECS logs"
 }
 }
}

path = Path("infrastructure/stepfunctions/definitions/bikepulse-dev-ingestion-pipeline.asl.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
print("INGESTION STATE MACHINE ASL: CREATED")
