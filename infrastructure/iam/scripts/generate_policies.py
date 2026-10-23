import json
import os
from pathlib import Path

account = os.environ["AWS_ACCOUNT_ID"]
region = os.environ["AWS_REGION"]

root = Path("infrastructure/iam")
trust_dir = root / "trust"
policy_dir = root / "policies"

trust_dir.mkdir(parents=True, exist_ok=True)
policy_dir.mkdir(parents=True, exist_ok=True)

bucket = f"bikepulse-{account}-{region}"
bucket_arn = f"arn:aws:s3:::{bucket}"
ecs_arn = f"arn:aws:ecs:{region}:{account}"
ddb_arn = f"arn:aws:dynamodb:{region}:{account}"
sfn_arn = f"arn:aws:states:{region}:{account}"
events_arn = f"arn:aws:events:{region}:{account}"
sns_arn = f"arn:aws:sns:{region}:{account}"

prefix = "bikepulse-dev"

def save_json(path, document):
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )

def statement(sid, actions, resources, condition=None):
    result = {
        "Sid": sid,
        "Effect": "Allow",
        "Action": actions,
        "Resource": resources
    }
    if condition:
        result["Condition"] = condition
    return result

def policy(*statements):
    return {
        "Version": "2012-10-17",
        "Statement": list(statements)
    }

def trust(service, source_arn):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": service
                },
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": account
                    },
                    "ArnLike": {
                        "aws:SourceArn": source_arn
                    }
                }
            }
        ]
    }

def objects(folder):
    return f"{bucket_arn}/{folder}/*"

registry = (
    f"{ddb_arn}:table/"
    "bikepulse-dev-model-registry"
)
prediction_table = (
    f"{ddb_arn}:table/"
    "bikepulse-dev-prediction-events"
)

def with_indexes(table_arn):
    return [
        table_arn,
        f"{table_arn}/index/*"
    ]

# --------------------------------------------------
# 1. IAM role trust policies
# --------------------------------------------------
ecs_trust = trust(
    "ecs-tasks.amazonaws.com",
    f"{ecs_arn}:*"
)

roles = {
    "bikepulse-dev-ecs-execution-role": ecs_trust,
    "bikepulse-dev-frontend-task-role": ecs_trust,
    "bikepulse-dev-api-task-role": ecs_trust,
    "bikepulse-dev-processor-task-role": ecs_trust,
    "bikepulse-dev-trainer-task-role": ecs_trust,
    "bikepulse-dev-monitor-task-role": ecs_trust,
    "bikepulse-dev-sfn-role": trust(
        "states.amazonaws.com",
        f"{sfn_arn}:stateMachine:bikepulse-dev-*"
    ),
    "bikepulse-dev-eventbridge-start-role": trust(
        "events.amazonaws.com",
        f"{events_arn}:rule/bikepulse-dev-s3-ingestion"
    ),
    "bikepulse-dev-scheduler-start-role": trust(
        "scheduler.amazonaws.com",
        (
            f"arn:aws:scheduler:{region}:{account}:"
            "schedule-group/default"
        )
    )
}

for role_name, document in roles.items():
    save_json(
        trust_dir / f"{role_name}.json",
        document
    )

# --------------------------------------------------
# 2. API role
# --------------------------------------------------
api_policy = policy(
    statement(
        "ReadModelArtifacts",
        ["s3:GetObject"],
        [objects("models")]
    ),
    statement(
        "ReadModelRegistry",
        ["dynamodb:GetItem", "dynamodb:Query"],
        with_indexes(registry)
    ),
    statement(
        "WriteAndReadPredictionEvents",
        [
            "dynamodb:PutItem",
            "dynamodb:GetItem",
            "dynamodb:Query"
        ],
        with_indexes(prediction_table)
    ),
    statement(
        "StartAdminWorkflows",
        ["states:StartExecution"],
        [
            (
                f"{sfn_arn}:stateMachine:"
                "bikepulse-dev-training-pipeline"
            ),
            (
                f"{sfn_arn}:stateMachine:"
                "bikepulse-dev-deployment-pipeline"
            )
        ]
    )
)

# --------------------------------------------------
# 3. Data processor role
# --------------------------------------------------
processor_policy = policy(
    statement(
        "ListDataBucket",
        ["s3:ListBucket"],
        [bucket_arn]
    ),
    statement(
        "ReadData",
        ["s3:GetObject"],
        [
            objects("raw"),
            objects("curated"),
            objects("features"),
            objects("splits")
        ]
    ),
    statement(
        "WriteProcessedData",
        ["s3:PutObject"],
        [
            objects("curated"),
            objects("features"),
            objects("splits")
        ]
    )
)

# --------------------------------------------------
# 4. Model trainer role
# --------------------------------------------------
trainer_policy = policy(
    statement(
        "ReadTrainingDataAndModels",
        ["s3:GetObject"],
        [
            objects("curated"),
            objects("features"),
            objects("splits"),
            objects("models")
        ]
    ),
    statement(
        "WriteModels",
        ["s3:PutObject"],
        [objects("models")]
    ),
    statement(
        "ReadModelRegistry",
        ["dynamodb:GetItem", "dynamodb:Query"],
        with_indexes(registry)
    ),
    statement(
        "RegisterCandidateModel",
        ["dynamodb:PutItem"],
        [registry]
    )
)

# --------------------------------------------------
# 5. Model monitor role
# --------------------------------------------------
monitor_policy = policy(
    statement(
        "ReadMonitoringData",
        ["s3:GetObject"],
        [
            objects("curated"),
            objects("features"),
            objects("models"),
            objects("prediction-logs"),
            objects("monitoring")
        ]
    ),
    statement(
        "WriteMonitoringReports",
        ["s3:PutObject"],
        [objects("monitoring")]
    ),
    statement(
        "ReadRegistryAndPredictionEvents",
        ["dynamodb:GetItem", "dynamodb:Query"],
        with_indexes(registry) + with_indexes(prediction_table)
    )
)

# --------------------------------------------------
# 6. Step Functions orchestration role
# --------------------------------------------------
task_definitions = [
    (
        f"{ecs_arn}:task-definition/"
        f"{prefix}-{component}-task:*"
    )
    for component in [
        "processor",
        "trainer",
        "monitor"
    ]
]

pass_roles = [
    (
        f"arn:aws:iam::{account}:role/"
        f"{prefix}-{component}"
    )
    for component in [
        "ecs-execution-role",
        "processor-task-role",
        "trainer-task-role",
        "monitor-task-role"
    ]
]

sfn_policy = policy(
    statement(
        "RunApprovedECSTasks",
        ["ecs:RunTask"],
        task_definitions
    ),
    statement(
        "ObserveAndStopStartedTasks",
        ["ecs:DescribeTasks", "ecs:StopTask"],
        ["*"]
    ),
    statement(
        "ManageECSSyncEvents",
        [
            "events:PutRule",
            "events:PutTargets",
            "events:DescribeRule"
        ],
        [
            (
                f"{events_arn}:rule/"
                "StepFunctionsGetEventsForECSTaskRule"
            )
        ]
    ),
    statement(
        "PassApprovedECSTaskRoles",
        ["iam:PassRole"],
        pass_roles,
        {
            "StringEquals": {
                "iam:PassedToService": "ecs-tasks.amazonaws.com"
            }
        }
    ),
    statement(
        "PublishOperationsAlerts",
        ["sns:Publish"],
        [f"{sns_arn}:bikepulse-dev-alerts"]
    ),
    statement(
        "WriteStepFunctionsCloudWatchLogs",
        [
            "logs:CreateLogDelivery",
            "logs:CreateLogStream",
            "logs:GetLogDelivery",
            "logs:UpdateLogDelivery",
            "logs:DeleteLogDelivery",
            "logs:ListLogDeliveries",
            "logs:PutLogEvents",
            "logs:PutResourcePolicy",
            "logs:DescribeResourcePolicies",
            "logs:DescribeLogGroups"
        ],
        ["*"]
    )
)

# --------------------------------------------------
# 7. EventBridge trigger role
# --------------------------------------------------
eventbridge_policy = policy(
    statement(
        "StartDataPipeline",
        ["states:StartExecution"],
        [
            (
                f"{sfn_arn}:stateMachine:"
                "bikepulse-dev-data-pipeline"
            )
        ]
    )
)

# --------------------------------------------------
# 8. Scheduler trigger role
# --------------------------------------------------
scheduler_policy = policy(
    statement(
        "StartMonitoringPipeline",
        ["states:StartExecution"],
        [
            (
                f"{sfn_arn}:stateMachine:"
                "bikepulse-dev-monitoring-pipeline"
            )
        ]
    )
)

policies = {
    "bikepulse-dev-api-task-role": api_policy,
    "bikepulse-dev-processor-task-role": processor_policy,
    "bikepulse-dev-trainer-task-role": trainer_policy,
    "bikepulse-dev-monitor-task-role": monitor_policy,
    "bikepulse-dev-sfn-role": sfn_policy,
    "bikepulse-dev-eventbridge-start-role": eventbridge_policy,
    "bikepulse-dev-scheduler-start-role": scheduler_policy
}

for role_name, document in policies.items():
    save_json(
        policy_dir / f"{role_name}.json",
        document
    )

print("=" * 55)
print("BIKEPULSE IAM POLICY GENERATION")
print("=" * 55)
print("AWS Account :", account)
print("AWS Region :", region)
print("S3 Bucket :", bucket)
print("Trust files :", len(roles))
print("Policy files:", len(policies))
print("=" * 55)
print("RESULT: GENERATED")
