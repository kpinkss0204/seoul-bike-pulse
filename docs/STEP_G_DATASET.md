# STEP G - Seoul Bike Dataset Ingestion
## 1. Dataset
Name:
Seoul Bike Sharing Demand
Repository:
UCI Machine Learning Repository
Dataset ID:
560
Source:
https://archive.ics.uci.edu/dataset/560/seoul%2Bbike%2Bsharing%2Bdemand
DOI:
10.24432/C5F62R
License:
Creative Commons Attribution 4.0 International
## 2. Dataset Structure
Original filename:
SeoulBikeData.csv
Expected rows:
8760
Expected columns:
14
Date start:
2017-12-01
Date end:
2018-11-30
Target:
Rented Bike Count
## 3. Dataset Version
Version:
dv001
Raw data must remain unchanged.
## 4. S3 Locations
Bucket:
bikepulse-<AWS_ACCOUNT_ID>-ap-northeast-2
Original CSV:
raw/source=uci-seoul-bike/version=dv001/SeoulBikeData.csv
Manifest:
raw/source=uci-seoul-bike/version=dv001/manifest.json
## 5. Validation
The ingestion validator checks:
- File decoding
- 8760 data rows
- 14 columns
- Required column structure
- Date range
- Hour range 0-23
- Duplicate timestamps
- 24 hourly records per day
- Missing fields
- Numeric value ranges
 AI MLOps 실무프로젝트 페이지 40
- Numeric value ranges
- Categorical values
- SHA-256 checksum
## 6. Data Security
- S3 Block Public Access enabled
- SSE-S3 encryption
- S3 Versioning enabled
- HTTPS-only bucket policy
- Original data preserved
- GitHub does not contain raw CSV
## 7. Data Processing Policy
Raw dataset:
Preserved unchanged.
Curated dataset:
Created in later processing stages.
Model training:
Only functioning-day records are used.
## 8. Event Processing
S3 EventBridge integration:
Not enabled in Step G.
Data pipeline automation:
Will be enabled after Step Functions and
the ECS Processor are implemented.
## 9. Reproducibility
SHA-256, source attribution, validation results,
and acquisition metadata are stored in S3 manifest.json.
