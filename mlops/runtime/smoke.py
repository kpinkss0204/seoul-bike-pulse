import json
import os
from datetime import datetime, timezone
import boto3

ROLE_KIND = os.environ["ROLE_KIND"]
BUCKET = os.environ["BIKEPULSE_BUCKET"]

s3 = boto3.client("s3")

def read_object(key):
    response = s3.get_object(
        Bucket=BUCKET,
        Key=key
    )
    data = response["Body"].read()
    print(
        f"S3_READ_PASSED: {key}, bytes={len(data)}",
        flush=True
    )

def write_object(key):
    payload = {
        "project": "bikepulse",
        "role_kind": ROLE_KIND,
        "test": "ecs-fargate-smoke",
        "status": "passed",
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat()
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json"
    )
    print(
        f"S3_WRITE_PASSED: {key}",
        flush=True
    )

def main():
    print(
        f"STARTING_MLOPS_TASK: {ROLE_KIND}",
        flush=True
    )
    if ROLE_KIND == "processor":
        read_object(
            "raw/"
        )
        write_object(
            "curated/_smoke_tests/processor.json"
        )
    elif ROLE_KIND == "trainer":
        read_object(
            "curated/_smoke_tests/processor.json"
        )
        write_object(
            "models/_smoke_tests/trainer.json"
        )
    elif ROLE_KIND == "monitor":
        read_object(
            "models/_smoke_tests/trainer.json"
        )
        write_object(
            "monitoring/_smoke_tests/monitor.json"
        )
    else:
        raise ValueError(
            f"Unsupported ROLE_KIND: {ROLE_KIND}"
        )
    print(
        f"{ROLE_KIND.upper()}_SMOKE_PASSED",
        flush=True
    )

if __name__ == "__main__":
    main()
