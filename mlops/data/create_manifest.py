import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "data/validation/dv001_report.json"
MANIFEST_PATH = ROOT / "data/validation/dv001_manifest.json"

def main():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    
    if report["validation_status"] != "PASSED":
        raise RuntimeError("Dataset validation must pass first.")
        
    manifest = {
        "project": "bikepulse",
        "dataset_name": "Seoul Bike Sharing Demand",
        "dataset_id": 560,
        "dataset_version": "dv001",
        "source": "UCI Machine Learning Repository",
        "source_url": "https://archive.ics.uci.edu/dataset/560/seoul%2Bbike%2Bsharing%2Bdemand",
        "doi": "10.24432/C5F62R",
        "license": "CC BY 4.0",
        "original_filename": report["filename"],
        "encoding": report["encoding"],
        "sha256": report["sha256"],
        "file_size_bytes": report["file_size_bytes"],
        "row_count": report["row_count"],
        "column_count": report["column_count"],
        "date_start": report["date_start"],
        "date_end": report["date_end"],
        "validation_status": report["validation_status"],
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_s3_key": "raw/source=uci-seoul-bike/version=dv001/SeoulBikeData.csv"
    }
    
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    
    print("MANIFEST CREATED")
    print("Dataset:", manifest["dataset_name"])
    print("Version:", manifest["dataset_version"])
    print("SHA256 :", manifest["sha256"])
    print("Rows :", manifest["row_count"])
    print("Status :", manifest["validation_status"])

if __name__ == "__main__":
    main()
