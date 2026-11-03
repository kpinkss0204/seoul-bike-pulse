import hashlib
import json
from pathlib import Path

root = Path("data/serving/package-config")
manifest = json.loads(
 (root / "package_manifest.json").read_text()
)
metadata = json.loads(
 (root / "metadata.json").read_text()
)

assert manifest["status"] == "PASSED"
assert manifest["model_version"] == "mv001"

entries = {
 item["path"]: item
 for item in manifest["files"]
}

for name, filename in [
 ("environment/requirements.txt", "requirements.txt"),
 ("metadata.json", "metadata.json")
]:
 content = (
  root / filename
 ).read_bytes()
 actual = hashlib.sha256(
  content
 ).hexdigest()
 assert actual == entries[name]["sha256"]
 assert len(content) == entries[name]["size_bytes"]

assert metadata["model_version"] == "mv001"
assert metadata["feature_count"] == 19

requirements = (
 root / "requirements.txt"
).read_text(encoding="utf-8")

extra = """
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
boto3>=1.35,<2.0
jsonschema>=4.23,<5.0
"""

target = Path(
 "backend/requirements-serving.txt"
)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
 requirements.rstrip()
 + "\n"
 + extra.strip()
 + "\n",
 encoding="utf-8"
)
print("SERVING REQUIREMENTS: CREATED")
print(target.read_text())
