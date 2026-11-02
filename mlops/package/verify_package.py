import argparse
import hashlib
import json
import math

from pathlib import Path

import joblib
import numpy as np
import pandas as pd


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def validate_value(name, value, rule):
    kind = rule["type"]

    if kind == "boolean":
        assert type(value) is bool, name

    elif kind == "integer":
        assert type(value) is int, name

    elif kind == "number":
        assert type(value) in (int, float), name
        assert math.isfinite(float(value)), name

    elif kind == "string":
        assert type(value) is str, name

    else:
        raise ValueError(
            f"Unknown schema type: {kind}"
        )

    if "enum" in rule:
        assert value in rule["enum"], name

    if "minimum" in rule:
        assert value >= rule["minimum"], name

    if "maximum" in rule:
        assert value <= rule["maximum"], name


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--package-root",
        required=True
    )

    args = parser.parse_args()

    root = Path(
        args.package_root
    )

    manifest = json.loads(
        (root / "package_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["status"] == "PASSED"
    assert manifest["model_id"] == "bike-demand"
    assert len(manifest["files"]) == 9

    for item in manifest["files"]:
        path = root / item["path"]

        assert path.is_file(), item["path"]

        data = path.read_bytes()

        assert sha256(data) == item["sha256"], (
            item["path"]
        )

        assert len(data) == item["size_bytes"], (
            item["path"]
        )

        print(
            "ARTIFACT_VERIFIED:",
            item["path"]
        )

    metadata = json.loads(
        (root / "metadata.json").read_text()
    )

    schema = json.loads(
        (root / "input_schema.json").read_text()
    )

    smoke = json.loads(
        (
            root / "examples/smoke_input.json"
        ).read_text()
    )

    expected = json.loads(
        (
            root / "examples/smoke_expected.json"
        ).read_text()
    )

    evaluation = json.loads(
        (root / "evaluation.json").read_text()
    )

    assert metadata["status"] == (
        "CANDIDATE_PENDING_REGISTRATION"
    )

    assert metadata["model_version"] == (
        manifest["model_version"]
    )

    assert metadata["model_sha256"] == (
        manifest["model_sha256"]
    )

    assert metadata[
        "model_retrained_for_packaging"
    ] is False

    assert evaluation["status"] == "PASSED"

    assert metadata["evaluation_run_id"] == (
        evaluation["evaluation_run_id"]
    )

    assert metadata["test_metrics"] == (
        evaluation["test_metrics"]
    )

    model_bytes = (
        root / "model.joblib"
    ).read_bytes()

    assert sha256(model_bytes) == (
        metadata["model_sha256"]
    )

    features = metadata["feature_columns"]

    assert len(features) == 19
    assert len(set(features)) == 19
    assert metadata["feature_count"] == 19

    assert schema["required"] == features
    assert schema["additionalProperties"] is False
    assert schema["input_mode"] == "feature-complete-19"

    payload = smoke["features"]

    assert smoke["feature_version"] == "fv001"

    assert set(payload) == set(features)

    for name in features:
        validate_value(
            name,
            payload[name],
            schema["properties"][name]
        )

    # Only load internally produced, verified Joblib files.
    pipeline = joblib.load(
        root / "model.joblib"
    )

    assert list(
        pipeline.feature_names_in_
    ) == features

    input_frame = pd.DataFrame(
        [payload],
        columns=features
    )

    prediction = float(
        pipeline.predict(
            input_frame
        )[0]
    )

    assert np.isfinite(prediction)

    assert np.isclose(
        prediction,
        expected["expected_prediction"],
        rtol=expected["rtol"],
        atol=expected["atol"]
    )

    print()
    print("MODEL:", metadata["model_name"])
    print("VERSION:", metadata["model_version"])
    print("FEATURE COUNT:", len(features))
    print("SMOKE PREDICTION:", prediction)

    print("MODEL PACKAGE VALIDATION: PASSED")


if __name__ == "__main__":
    main()
