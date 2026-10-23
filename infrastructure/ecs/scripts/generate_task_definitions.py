import json
import os
from pathlib import Path

ACCOUNT = os.environ["AWS_ACCOUNT_ID"]
REGION = os.environ["AWS_REGION"]
TAG = os.environ["IMAGE_TAG"]
BUCKET = os.environ["BIKEPULSE_BUCKET"]
ECR = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
ROLE_PREFIX = f"arn:aws:iam::{ACCOUNT}:role"

OUTPUT = Path("infrastructure/ecs/task-definitions")
OUTPUT.mkdir(parents=True, exist_ok=True)

COMPONENTS = {
    "frontend": {
        "family": "bikepulse-dev-frontend-task",
        "task_role": "bikepulse-dev-frontend-task-role",
        "port": 80
    },
    "api": {
        "family": "bikepulse-dev-api-task",
        "task_role": "bikepulse-dev-api-task-role",
        "port": 8000
    },
    "processor": {
        "family": "bikepulse-dev-processor-task",
        "task_role": "bikepulse-dev-processor-task-role",
        "port": None
    },
    "trainer": {
        "family": "bikepulse-dev-trainer-task",
        "task_role": "bikepulse-dev-trainer-task-role",
        "port": None
    },
    "monitor": {
        "family": "bikepulse-dev-monitor-task",
        "task_role": "bikepulse-dev-monitor-task-role",
        "port": None
    }
}

for component, config in COMPONENTS.items():
    container = {
        "name": component,
        "image": f"{ECR}/bikepulse/{component}:{TAG}",
        "essential": True,
        "environment": [
            {
                "name": "BIKEPULSE_BUCKET",
                "value": BUCKET
            },
            {
                "name": "AWS_DEFAULT_REGION",
                "value": REGION
            }
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": f"/ecs/bikepulse-dev/{component}",
                "awslogs-region": REGION,
                "awslogs-stream-prefix": "ecs"
            }
        }
    }
    
    if config["port"] is not None:
        container["portMappings"] = [
            {
                "containerPort": config["port"],
                "protocol": "tcp"
            }
        ]
        
    document = {
        "family": config["family"],
        "networkMode": "awsvpc",
        "requiresCompatibilities": [
            "FARGATE"
        ],
        "cpu": "256",
        "memory": "512",
        "executionRoleArn": f"{ROLE_PREFIX}/bikepulse-dev-ecs-execution-role",
        "taskRoleArn": f"{ROLE_PREFIX}/{config['task_role']}",
        "runtimePlatform": {
            "cpuArchitecture": "X86_64",
            "operatingSystemFamily": "LINUX"
        },
        "containerDefinitions": [
            container
        ]
    }
    
    path = OUTPUT / f"{component}.json"
    path.write_text(
        json.dumps(
            document,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )
    print(f"GENERATED: {path}")

print("ALL TASK DEFINITIONS GENERATED")
