import argparse
import hashlib
import json
import os
import re
import sys

from datetime import datetime, timezone
from decimal import Decimal

import boto3

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError


MODEL_ID = "bike-demand"

PACKAGE_ROOT = (
    "models/registry-candidates/"
    "model_id=bike-demand/"
)

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

KNOWN_MODELS = {
    "dummy",
    "ridge",
    "random_forest",
    "xgb_default",
    "xgb_grid",
    "xgb_random"
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def sha256(data):
    return hashlib.sha256(
        data
    ).hexdigest()


def parse_json(content):
    return json.loads(
        content,
        parse_float=Decimal
    )


def printable(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    if isinstance(value, dict):
        return {
            key: printable(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            printable(item)
            for item in value
        ]

    return value


def show(label, document):
    print(
        label,
        json.dumps(
            printable(document),
            ensure_ascii=False,
            indent=2
        ),
        flush=True
    )


def validate_version(value):
    require(
        bool(re.fullmatch(r"mv\d{3}", value)),
        "INVALID_MODEL_VERSION"
    )


def package_prefix(version):
    validate_version(version)

    return (
        PACKAGE_ROOT
        + f"version={version}/"
    )


def download(s3, bucket, key):
    return s3.get_object(
        Bucket=bucket,
        Key=key
    )["Body"].read()


def verify_package(s3, bucket, version):
    prefix = package_prefix(version)

    manifest_key = (
        prefix + "package_manifest.json"
    )

    manifest_bytes = download(
        s3,
        bucket,
        manifest_key
    )

    manifest = parse_json(
        manifest_bytes
    )

    require(
        manifest.get("status") == "PASSED",
        "PACKAGE_MANIFEST_NOT_PASSED"
    )

    require(
        manifest.get("model_id") == MODEL_ID
        and manifest.get("model_version") == version,
        "PACKAGE_VERSION_MISMATCH"
    )

    entries = manifest.get("files", [])

    require(
        len(entries) == 9,
        "PACKAGE_FILE_COUNT_MISMATCH"
    )

    paths = [
        item["path"]
        for item in entries
    ]

    require(
        set(paths) == EXPECTED_FILES
        and len(paths) == len(set(paths)),
        "PACKAGE_FILE_LIST_MISMATCH"
    )

    verified = {}

    for item in entries:
        name = item["path"]

        content = download(
            s3,
            bucket,
            prefix + name
        )

        require(
            sha256(content) == item["sha256"],
            f"PACKAGE_SHA256_MISMATCH: {name}"
        )

        require(
            len(content) == item["size_bytes"],
            f"PACKAGE_SIZE_MISMATCH: {name}"
        )

        verified[name] = content

    metadata = parse_json(
        verified["metadata.json"]
    )

    evaluation = parse_json(
        verified["evaluation.json"]
    )

    schema = parse_json(
        verified["input_schema.json"]
    )

    model_sha = sha256(
        verified["model.joblib"]
    )

    require(
        manifest["model_sha256"] == model_sha
        and metadata["model_sha256"] == model_sha,
        "MODEL_SHA256_MISMATCH"
    )

    require(
        metadata.get("status")
        == "CANDIDATE_PENDING_REGISTRATION",
        "PACKAGE_NOT_A_REGISTRY_CANDIDATE"
    )

    require(
        metadata.get("model_id") == MODEL_ID
        and metadata.get("model_version") == version,
        "METADATA_MODEL_ID_MISMATCH"
    )

    require(
        metadata.get("model_name") in KNOWN_MODELS,
        "UNKNOWN_MODEL_TYPE"
    )

    require(
        metadata.get("dataset_version") == "dv001"
        and metadata.get("feature_version") == "fv001"
        and metadata.get("feature_count") == 19,
        "MODEL_FEATURE_VERSION_MISMATCH"
    )

    columns = metadata.get(
        "feature_columns", []
    )

    require(
        len(columns) == 19
        and len(set(columns)) == 19,
        "INVALID_FEATURE_COLUMNS"
    )

    require(
        schema.get("required") == columns
        and schema.get("schema_version") == "fv001"
        and schema.get("input_mode")
        == "feature-complete-19"
        and schema.get("additionalProperties") is False,
        "INPUT_SCHEMA_MISMATCH"
    )

    require(
        metadata.get("model_retrained_for_packaging")
        is False,
        "MODEL_WAS_RETRAINED"
    )

    require(
        metadata.get("model_key")
        == prefix + "model.joblib",
        "MODEL_S3_KEY_MISMATCH"
    )

    require(
        metadata.get("source_model_sha256")
        == model_sha,
        "SOURCE_MODEL_SHA256_MISMATCH"
    )

    require(
        evaluation.get("status") == "PASSED"
        and evaluation.get("test_evaluated") is True
        and evaluation.get("model_retrained") is False
        and evaluation.get("model_changed_after_test")
        is False,
        "FINAL_EVALUATION_NOT_VALID"
    )

    require(
        evaluation.get("model_sha256") == model_sha
        and evaluation.get("model_name")
        == metadata["model_name"],
        "EVALUATED_MODEL_MISMATCH"
    )

    require(
        evaluation.get("selection_id")
        == metadata["selection_id"]
        and evaluation.get("evaluation_run_id")
        == metadata["evaluation_run_id"],
        "MODEL_EVALUATION_LINEAGE_MISMATCH"
    )

    require(
        evaluation.get("test_metrics")
        == metadata.get("test_metrics"),
        "EVALUATION_METRICS_MISMATCH"
    )

    # Record actual S3 object version IDs when available.
    model_head = s3.head_object(
        Bucket=bucket,
        Key=prefix + "model.joblib"
    )

    manifest_head = s3.head_object(
        Bucket=bucket,
        Key=manifest_key
    )

    files_by_path = {
        item["path"]: item
        for item in entries
    }

    result = {
        "prefix": prefix,
        "manifest_key": manifest_key,
        "manifest_sha256": sha256(manifest_bytes),
        "manifest_s3_version_id": (
            manifest_head.get("VersionId")
        ),
        "model_s3_version_id": (
            model_head.get("VersionId")
        ),
        "model_sha256": model_sha,
        "schema_sha256": (
            files_by_path["input_schema.json"]["sha256"]
        ),
        "metadata": metadata,
        "schema": schema,
        "evaluation": evaluation
    }

    print(
        "MODEL_PACKAGE_VALIDATION: PASSED",
        flush=True
    )

    return result


def create_registry_item(
    package,
    bucket,
    version
):
    meta = package["metadata"]
    scores = package["evaluation"]["test_metrics"]

    now = utc_now()

    compatibility = (
        "READY"
        if package["schema"].get(
            "frontend_8_field_contract_supported"
        ) is True
        else "BLOCKED_FRONTEND_8_INPUT"
    )

    item = {
        "model_id": MODEL_ID,
        "model_version": version,
        "item_type": "MODEL",
        "status": "CANDIDATE",
        "approval_status": "PENDING",
        "deployment_status": "NOT_DEPLOYED",
        "compatibility_status": compatibility,
        "registered_at": now,
        "updated_at": now,
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "model_name": meta["model_name"],
        "feature_count": 19,
        "model_s3_bucket": bucket,
        "model_s3_key": (
            package["prefix"] + "model.joblib"
        ),
        "model_sha256": package["model_sha256"],
        "package_prefix": package["prefix"],
        "package_manifest_key": package["manifest_key"],
        "package_manifest_sha256": (
            package["manifest_sha256"]
        ),
        "input_schema_sha256": (
            package["schema_sha256"]
        ),
        "selection_id": meta["selection_id"],
        "evaluation_run_id": meta["evaluation_run_id"],
        "training_run_id": meta["training_run_id"],
        "training_stage": meta["training_stage"],
        "train_mae": meta["train_mae"],
        "validation_mae": meta["validation_mae"],
        "test_mae": scores["mae"],
        "test_rmse": scores["rmse"],
        "test_r2": scores["r2"]
    }

    if package["model_s3_version_id"]:
        item["model_s3_version_id"] = (
            package["model_s3_version_id"]
        )

    if package["manifest_s3_version_id"]:
        item["package_manifest_s3_version_id"] = (
            package["manifest_s3_version_id"]
        )

    return item


def get_item(table, version):
    result = table.get_item(
        Key={
            "model_id": MODEL_ID,
            "model_version": version
        },
        ConsistentRead=True
    )

    return result.get("Item")


def list_versions(table):
    records = []
    params = {
        "KeyConditionExpression": (
            Key("model_id").eq(MODEL_ID)
            & Key("model_version").begins_with("mv")
        ),
        "ConsistentRead": True
    }

    while True:
        response = table.query(**params)

        records.extend(
            response.get("Items", [])
        )

        last_key = response.get(
            "LastEvaluatedKey"
        )

        if not last_key:
            break

        params["ExclusiveStartKey"] = last_key

    return [
        item
        for item in records
        if item.get("item_type") == "MODEL"
    ]


serializer = TypeSerializer()


def dynamodb_map(document):
    return {
        key: serializer.serialize(value)
        for key, value in document.items()
    }


def move_to_staging(client, table_name, version):
    now = utc_now()

    alias = {
        "model_id": MODEL_ID,
        "model_version": "ALIAS#STAGING",
        "item_type": "ALIAS",
        "target_version": version,
        "created_at": now,
        "updated_at": now
    }

    client.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": table_name,
                    "Key": dynamodb_map({
                        "model_id": MODEL_ID,
                        "model_version": version
                    }),
                    "UpdateExpression": (
                        "SET #s = :staging, "
                        "updated_at = :now"
                    ),
                    "ConditionExpression": (
                        "#s = :candidate "
                        "AND approval_status = :pending "
                        "AND item_type = :model"
                    ),
                    "ExpressionAttributeNames": {
                        "#s": "status"
                    },
                    "ExpressionAttributeValues": (
                        dynamodb_map({
                            ":candidate": "CANDIDATE",
                            ":staging": "STAGING",
                            ":pending": "PENDING",
                            ":model": "MODEL",
                            ":now": now
                        })
                    )
                }
            },
            {
                "Put": {
                    "TableName": table_name,
                    "Item": dynamodb_map(alias),
                    "ConditionExpression": (
                        "attribute_not_exists(model_id)"
                    )
                }
            }
        ]
    )

    print(
        "STAGING_TRANSITION: PASSED",
        flush=True
    )


def remove_from_staging(
    client,
    table_name,
    version
):
    now = utc_now()

    client.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": table_name,
                    "Key": dynamodb_map({
                        "model_id": MODEL_ID,
                        "model_version": version
                    }),
                    "UpdateExpression": (
                        "SET #s = :candidate, "
                        "updated_at = :now"
                    ),
                    "ConditionExpression": (
                        "#s = :staging "
                        "AND item_type = :model"
                    ),
                    "ExpressionAttributeNames": {
                        "#s": "status"
                    },
                    "ExpressionAttributeValues": (
                        dynamodb_map({
                            ":candidate": "CANDIDATE",
                            ":staging": "STAGING",
                            ":model": "MODEL",
                            ":now": now
                        })
                    )
                }
            },
            {
                "Delete": {
                    "TableName": table_name,
                    "Key": dynamodb_map({
                        "model_id": MODEL_ID,
                        "model_version": "ALIAS#STAGING"
                    }),
                    "ConditionExpression": (
                        "target_version = :version"
                    ),
                    "ExpressionAttributeValues": (
                        dynamodb_map({
                            ":version": version
                        })
                    )
                }
            }
        ]
    )

    print(
        "STAGING_REMOVAL: PASSED",
        flush=True
    )


def archive_candidate(table, version):
    table.update_item(
        Key={
            "model_id": MODEL_ID,
            "model_version": version
        },
        UpdateExpression=(
            "SET #s = :archived, "
            "updated_at = :now"
        ),
        ConditionExpression=(
            "#s = :candidate "
            "AND item_type = :model"
        ),
        ExpressionAttributeNames={
            "#s": "status"
        },
        ExpressionAttributeValues={
            ":archived": "ARCHIVED",
            ":candidate": "CANDIDATE",
            ":model": "MODEL",
            ":now": utc_now()
        }
    )

    print(
        "CANDIDATE_ARCHIVED: PASSED",
        flush=True
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "validate",
            "register",
            "get",
            "list",
            "stage",
            "unstage",
            "production",
            "archive"
        ]
    )

    parser.add_argument(
        "--table",
        default=os.environ.get(
            "REGISTRY_TABLE",
            "bikepulse-dev-model-registry"
        )
    )

    parser.add_argument(
        "--bucket",
        default=os.environ.get(
            "BIKEPULSE_BUCKET"
        )
    )

    parser.add_argument(
        "--model-version",
        default=os.environ.get(
            "MODEL_VERSION",
            "mv001"
        )
    )

    args = parser.parse_args()

    validate_version(
        args.model_version
    )

    session = boto3.Session(
        region_name=os.environ.get(
            "AWS_REGION",
            "ap-northeast-2"
        )
    )

    s3 = session.client("s3")
    client = session.client("dynamodb")

    table = session.resource(
        "dynamodb"
    ).Table(args.table)

    try:
        if args.action in ("validate", "register"):
            require(
                bool(args.bucket),
                "BIKEPULSE_BUCKET_REQUIRED"
            )

            package = verify_package(
                s3,
                args.bucket,
                args.model_version
            )

            if args.action == "validate":
                show(
                    "VALIDATED_MODEL:",
                    {
                        "model_version": args.model_version,
                        "model_name": (
                            package["metadata"]["model_name"]
                        ),
                        "model_sha256": (
                            package["model_sha256"]
                        ),
                        "test_mae": (
                            package["evaluation"]
                            ["test_metrics"]["mae"]
                        )
                    }
                )

                return 0

            item = create_registry_item(
                package,
                args.bucket,
                args.model_version
            )

            table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(model_id) "
                    "AND attribute_not_exists(model_version)"
                )
            )

            print(
                "MODEL_REGISTRATION: PASSED",
                flush=True
            )

            show("REGISTERED_MODEL:", item)

        elif args.action == "get":
            item = get_item(
                table,
                args.model_version
            )

            require(
                item is not None,
                "MODEL_VERSION_NOT_FOUND"
            )

            show("MODEL_RECORD:", item)

        elif args.action == "list":
            items = list_versions(table)

            show(
                "REGISTERED_VERSIONS:",
                items
            )

        elif args.action == "stage":
            move_to_staging(
                client,
                args.table,
                args.model_version
            )

        elif args.action == "unstage":
            remove_from_staging(
                client,
                args.table,
                args.model_version
            )

        elif args.action == "archive":
            archive_candidate(
                table,
                args.model_version
            )

        elif args.action == "production":
            alias = get_item(
                table,
                "ALIAS#PRODUCTION"
            )

            if alias is None:
                print(
                    "PRODUCTION_ALIAS: NOT_SET",
                    flush=True
                )
            else:
                show(
                    "PRODUCTION_ALIAS:",
                    alias
                )

        return 0

    except ClientError as exc:
        code = exc.response["Error"]["Code"]

        if code == "ConditionalCheckFailedException":
            print(
                "CONDITIONAL_WRITE_REJECTED",
                flush=True
            )

        elif code == "TransactionCanceledException":
            print(
                "TRANSACTION_REJECTED",
                flush=True
            )

        print(
            "REGISTRY_AWS_ERROR:",
            code,
            exc.response["Error"].get(
                "Message", ""
            ),
            flush=True
        )

        return 1

    except Exception as exc:
        print(
            "REGISTRY_ERROR:",
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
