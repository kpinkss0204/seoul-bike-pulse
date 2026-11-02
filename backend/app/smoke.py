import json
import os
import subprocess
import sys
import time
import urllib.request
import boto3

def verify_http():
    url = "http://127.0.0.1:8000/api/v1/health"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000"
        ]
    )
    try:
        for attempt in range(30):
            try:
                with urllib.request.urlopen(
                    url,
                    timeout=3
                ) as response:
                    data = json.load(response)
                    assert response.status == 200
                    assert data["status"] == "ok"
                    assert data["service"] == "bikepulse-api"
                    print(
                        "API_HTTP_SMOKE_PASSED",
                        flush=True
                    )
                    return
            except (
                OSError,
                ValueError,
                AssertionError
            ):
                time.sleep(1)
        raise RuntimeError(
            "FastAPI health check failed"
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

def verify_s3():
    bucket = os.environ["BIKEPULSE_BUCKET"]
    client = boto3.client("s3")
    response = client.get_object(
        Bucket=bucket,
        Key="models/"
    )
    response["Body"].read()
    print(
        "API_S3_READ_PASSED",
        flush=True
    )

if __name__ == "__main__":
    verify_http()
    verify_s3()
    print(
        "API_SMOKE_PASSED",
        flush=True
    )
