import argparse
import json
from pathlib import Path
import pandas as pd

def validate(root):
    feature_root = (
        root / "features/version=fv001"
    )
    split_root = (
        root / "splits/version=dv001/"
        "feature_version=fv001"
    )
    
    feature_manifest = json.loads(
        (
            feature_root / "feature_manifest.json"
        ).read_text(encoding="utf-8")
    )
    split_manifest = json.loads(
        (
            split_root / "split_manifest.json"
        ).read_text(encoding="utf-8")
    )
    
    features = pd.read_parquet(
        feature_root / "features.parquet"
    )
    
    assert feature_manifest["status"] == "PASSED"
    assert split_manifest["status"] == "PASSED"
    assert len(features) == 8760
    
    model_columns = (
        feature_manifest["model_feature_columns"]
    )
    assert len(model_columns) == 19
    assert "rented_bike_count" not in model_columns
    assert "is_functioning_day" not in model_columns
    assert "sample_id" not in model_columns
    assert "timestamp" not in model_columns
    
    all_ids = []
    ranges = {}
    for name in [
        "train",
        "validation",
        "test"
    ]:
        X = pd.read_parquet(
            split_root / f"X_{name}.parquet"
        )
        y = pd.read_parquet(
            split_root / f"y_{name}.parquet"
        )
        
        assert len(X) > 0
        assert len(X) == len(y)
        assert X["sample_id"].equals(
            y["sample_id"]
        )
        assert not X["sample_id"].duplicated().any()
        assert list(X.columns) == (
            ["sample_id", "timestamp"]
            + model_columns
        )
        assert list(y.columns) == [
            "sample_id",
            "rented_bike_count"
        ]
        assert X[model_columns].notna().all().all()
        assert (y["rented_bike_count"] >= 0).all()
        assert X["timestamp"].is_monotonic_increasing
        
        ranges[name] = (
            X["timestamp"].min(),
            X["timestamp"].max()
        )
        
        expected_rows = (
            split_manifest["split_statistics"]
            [name]["rows"]
        )
        assert len(X) == expected_rows
        all_ids.extend(
            X["sample_id"].tolist()
        )
        print(
            f"{name.upper()}: "
            f"X={X.shape}, y={y.shape}, "
            f"PERIOD={ranges[name]}"
        )
        
    assert (
        ranges["train"][1]
        < ranges["validation"][0]
    )
    assert (
        ranges["validation"][1]
        < ranges["test"][0]
    )
    assert len(all_ids) == len(set(all_ids))
    
    eligible_ids = set(
        features.loc[
            features["is_functioning_day"],
            "sample_id"
        ]
    )
    assert set(all_ids) == eligible_ids
    assert len(all_ids) == (
        split_manifest["total_split_rows"]
    )
    print("FEATURE SPLIT VALIDATION: PASSED")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default="data/features/l_local"
    )
    args = parser.parse_args()
    validate(
        Path(args.root)
    )

if __name__ == "__main__":
    main()
