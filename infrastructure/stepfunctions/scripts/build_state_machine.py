import json
import os
from pathlib import Path
import boto3

REGION = os.environ["AWS_REGION"]
ACCOUNT = os.environ["AWS_ACCOUNT_ID"]
ecs = boto3.client(
    "ecs",
    region_name=REGION
)

def find_task_definition(image_tag):
    paginator = ecs.get_paginator(
        "list_task_definitions"
    )
    matches = []
    for page in paginator.paginate(
        familyPrefix="bikepulse-dev-trainer-task",
        status="ACTIVE"
    ):
        for arn in page["taskDefinitionArns"]:
            doc = ecs.describe_task_definition(
                taskDefinition=arn
            )["taskDefinition"]
            containers = doc["containerDefinitions"]
            if len(containers) != 1:
                continue
            container = containers[0]
            image = container["image"]
            if image.endswith(
                f"/bikepulse/trainer:{image_tag}"
            ):
                if container["name"] != "trainer":
                    raise ValueError(
                        f"Unexpected container name: {arn}"
                    )
                matches.append(arn)
    if not matches:
        raise ValueError(
            f"No ECS Task Definition for "
            f"trainer:{image_tag}"
        )
    # Existing revisions are returned in revision order.
    matches.sort(
        key=lambda arn: int(
            arn.rsplit(":", 1)[-1]
        ),
        reverse=True
    )
    print(
        f"trainer:{image_tag} -> {matches[0]}"
    )
    return matches[0]

cluster = ecs.describe_clusters(
    clusters=[
        os.environ["ECS_CLUSTER"]
    ]
)["clusters"][0]
if cluster["status"] != "ACTIVE":
    raise ValueError("ECS cluster is not ACTIVE")
cluster_arn = cluster["clusterArn"]
gate_task_arn = os.environ[
    "GATE_TASK_DEF_ARN"
]
m_task_arn = find_task_definition(
    os.environ["M_IMAGE_TAG"]
)
n_task_arn = find_task_definition(
    os.environ["N_IMAGE_TAG"]
)
o_task_arn = find_task_definition(
    os.environ["O_IMAGE_TAG"]
)

def environment(mapping):
    return [
        {
            "Name": name,
            "Value.$": source
        }
        for name, source in mapping.items()
    ]

def ecs_task(
    task_arn,
    container_name,
    command,
    variables,
    next_state,
    timeout=1800
):
    result = {
        "Type": "Task",
        "Resource": (
            "arn:aws:states:::ecs:runTask.sync"
        ),
        "Parameters": {
            "Cluster": cluster_arn,
            "TaskDefinition": task_arn,
            "LaunchType": "FARGATE",
            "PlatformVersion": "LATEST",
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                    "Subnets": [
                        os.environ["PRIVATE_A"]
                    ],
                    "SecurityGroups": [
                        os.environ["SG_MLOPS"]
                    ],
                    "AssignPublicIp": "DISABLED"
                }
            },
            "Overrides": {
                "ContainerOverrides": [
                    {
                        "Name": container_name,
                        "Command": command,
                        "Environment": environment(
                            variables
                        )
                    }
                ]
            }
        },
        "TimeoutSeconds": timeout,
        "ResultPath": "$.taskResult",
        "Retry": [
            {
                "ErrorEquals": [
                    "AmazonECS.Unknown"
                ],
                "IntervalSeconds": 10,
                "MaxAttempts": 2,
                "BackoffRate": 2
            }
        ],
        "Catch": [
            {
                "ErrorEquals": [
                    "States.ALL"
                ],
                "ResultPath": "$.taskError",
                "Next": "PipelineFailed"
            }
        ],
        "Next": next_state
    }
    return result

def exit_check(next_state):
    # ecs:runTask.sync returns a single ECS task object directly.
    # Example: $.taskResult.Containers[0].ExitCode == 0
    # IsPresent protects against a missing exit code on failed startup.
    exit_code_path = "$.taskResult.Containers[0].ExitCode"
    return {
        "Type": "Choice",
        "Choices": [
            {
                "And": [
                    {
                        "Variable": exit_code_path,
                        "IsPresent": True
                    },
                    {
                        "Variable": exit_code_path,
                        "IsNumeric": True
                    },
                    {
                        "Variable": exit_code_path,
                        "NumericEquals": 0
                    }
                ],
                "Next": next_state
            }
        ],
        "Default": "PipelineFailed"
    }

def add_job(
    states,
    state_name,
    task_arn,
    container_name,
    command,
    variables,
    next_state,
    timeout=1800
):
    check_name = state_name + "ExitCheck"
    states[state_name] = ecs_task(
        task_arn,
        container_name,
        command,
        variables,
        check_name,
        timeout
    )
    states[check_name] = exit_check(
        next_state
    )

states = {
    "CheckInput": {
        "Type": "Choice",
        "Choices": [
            {
                "Variable": "$.simulate_failure",
                "BooleanEquals": True,
                "Next": "IntentionalFailure"
            },
            {
                "Variable": "$.mode",
                "StringEquals": "validate_only",
                "Next": "RawQualityGate"
            },
            {
                "Variable": "$.mode",
                "StringEquals": "train_candidate",
                "Next": "RawQualityGate"
            }
        ],
        "Default": "PipelineFailed"
    },
    "PipelineFailed": {
        "Type": "Fail",
        "Error": "BikePulsePipelineFailed",
        "Cause": (
            "Review the preceding state and ECS CloudWatch Logs"
        )
    },
    "ValidationCompleted": {
        "Type": "Succeed"
    },
    "CandidateTrainingCompleted": {
        "Type": "Succeed"
    }
}

# A. Raw data gate
add_job(
    states,
    "RawQualityGate",
    gate_task_arn,
    "processor",
    ["--gate", "raw"],
    {},
    "CuratedQualityGate",
    900
)

# B. Curated data gate
add_job(
    states,
    "CuratedQualityGate",
    gate_task_arn,
    "processor",
    ["--gate", "curated"],
    {},
    "FeatureQualityGate",
    900
)

# C. Feature and Split gate
add_job(
    states,
    "FeatureQualityGate",
    gate_task_arn,
    "processor",
    ["--gate", "feature"],
    {
        "FEATURE_REPORT_KEY": (
            "$.feature_report_key"
        )
    },
    "ChoosePipelineMode",
    900
)

states["ChoosePipelineMode"] = {
    "Type": "Choice",
    "Choices": [
        {
            "Variable": "$.mode",
            "StringEquals": "validate_only",
            "Next": "ValidationCompleted"
        },
        {
            "Variable": "$.mode",
            "StringEquals": "train_candidate",
            "Next": "TrainBaseline"
        }
    ],
    "Default": "PipelineFailed"
}

# D. Step M - baseline training
add_job(
    states,
    "TrainBaseline",
    m_task_arn,
    "trainer",
    [
        "python",
        "-u",
        "/app/training_worker.py"
    ],
    {
        "FEATURE_REPORT_KEY": (
            "$.feature_report_key"
        ),
        "TRAIN_RUN_ID": (
            "$.m_run_id"
        )
    },
    "BaselineQualityGate",
    3600
)

add_job(
    states,
    "BaselineQualityGate",
    gate_task_arn,
    "processor",
    ["--gate", "baseline"],
    {
        "FEATURE_REPORT_KEY": (
            "$.feature_report_key"
        ),
        "TRAIN_RUN_ID": (
            "$.m_run_id"
        )
    },
    "TuneXGBoost",
    900
)

# E. Step N - XGBoost search
add_job(
    states,
    "TuneXGBoost",
    n_task_arn,
    "trainer",
    [
        "python",
        "-u",
        "/app/xgboost_worker.py"
    ],
    {
        "FEATURE_REPORT_KEY": (
            "$.feature_report_key"
        ),
        "BASELINE_RUN_ID": (
            "$.m_run_id"
        ),
        "XGB_RUN_ID": (
            "$.n_run_id"
        )
    },
    "XGBoostQualityGate",
    14400
)

add_job(
    states,
    "XGBoostQualityGate",
    gate_task_arn,
    "processor",
    ["--gate", "xgboost"],
    {
        "FEATURE_REPORT_KEY": (
            "$.feature_report_key"
        ),
        "TRAIN_RUN_ID": (
            "$.m_run_id"
        ),
        "XGB_RUN_ID": (
            "$.n_run_id"
        )
    },
    "LockSelectedModel",
    900
)

# F. Step O - selection only, no Test
add_job(
    states,
    "LockSelectedModel",
    o_task_arn,
    "trainer",
    [
        "--mode",
        "lock"
    ],
    {
        "FEATURE_REPORT_KEY": (
            "$.feature_report_key"
        ),
        "BASELINE_RUN_ID": (
            "$.m_run_id"
        ),
        "XGB_RUN_ID": (
            "$.n_run_id"
        ),
        "SELECTION_ID": (
            "$.selection_id"
        )
    },
    "SelectionQualityGate",
    900
)

add_job(
    states,
    "SelectionQualityGate",
    gate_task_arn,
    "processor",
    ["--gate", "selection"],
    {
        "TRAIN_RUN_ID": (
            "$.m_run_id"
        ),
        "XGB_RUN_ID": (
            "$.n_run_id"
        ),
        "SELECTION_ID": (
            "$.selection_id"
        )
    },
    "CandidateTrainingCompleted",
    900
)

# G. Controlled failure test
add_job(
    states,
    "IntentionalFailure",
    gate_task_arn,
    "processor",
    ["--gate", "fail"],
    {},
    "PipelineFailed",
    300
)

definition = {
    "Comment": (
        "BikePulse Step R - immutable data checks "
        "and optional candidate training"
    ),
    "StartAt": "CheckInput",
    "States": states,
    "TimeoutSeconds": 28800
}
path = Path(
    "infrastructure/stepfunctions/definitions/"
    "bikepulse-dev-data-pipeline.asl.json"
)
path.parent.mkdir(
    parents=True,
    exist_ok=True
)
path.write_text(
    json.dumps(
        definition,
        ensure_ascii=False,
        indent=2
    ) + "\n",
    encoding="utf-8"
)
print()
print("STATE MACHINE DEFINITION: CREATED")
print("STATES:", len(states))
print("OUTPUT:", path)
