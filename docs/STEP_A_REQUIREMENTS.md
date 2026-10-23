# SEOUL BIKE PULSE
## 1. 프로젝트 개요
프로젝트명: SEOUL BIKE PULSE
목표:
- 서울 따릉이 시간별 대여량 예측
- React 기반 웹 대시보드
- Amazon ECS 기반 Frontend/Backend
- AWS Step Functions 기반 MLOps 자동화
- 머신러닝 모델의 버전 관리
- 데이터 드리프트 감지 및 재학습
- 모델 배포 및 롤백
- CI/CD 및 IaC 자동화
## 2. AWS 운영 환경
Region: ap-northeast-2
Environment: dev
Timezone: Asia/Seoul
Project ID: bikepulse
## 3. 데이터셋
Name: Seoul Bike Sharing Demand
Source: UCI Machine Learning Repository
Dataset ID: 560
Original file: SeoulBikeData.csv
Original rows: 8760
Original columns: 14
Target: rented_bike_count
Prediction unit:
서울 전체의 시간별 자전거 대여 건수
## 4. 머신러닝
Baseline:
 AI MLOps 실무프로젝트 페이지 18
Baseline:
- Linear Regression 또는 Ridge
Candidates:
- Random Forest Regressor
- XGBoost Regressor
Evaluation:
- MAE
- RMSE
- R2
Train:
2017-12-01 ~ 2018-07-31
Validation:
2018-08-01 ~ 2018-09-30
Test:
2018-10-01 ~ 2018-11-30
## 5. 애플리케이션
Frontend:
- React
- TypeScript
- Tailwind CSS
- Nginx
- Amazon ECS Fargate
Backend:
- Python FastAPI
- Scikit-learn
- XGBoost
- Amazon ECS Fargate
## 6. MLOps
Orchestrator:
AWS Step Functions
Workflow Type:
Standard
Pipelines:
- Data Pipeline
- Training Pipeline
- Deployment Pipeline
- Monitoring Pipeline
## 7. 데이터 관리
Data Lake:
Amazon S3
Analysis:
AWS Glue
Amazon Athena
Model Registry:
Amazon S3
Amazon DynamoDB
 AI MLOps 실무프로젝트 페이지 19
Amazon DynamoDB
## 8. 운영 및 자동화
Monitoring:
Amazon CloudWatch
Events:
Amazon EventBridge
EventBridge Scheduler
Notifications:
Amazon SNS
CI/CD:
GitHub
AWS CodePipeline
AWS CodeBuild
IaC:
AWS CloudFormation
Authentication:
Amazon Cognito
## 9. 프로젝트 정책
- AWS AI 관리형 서비스 사용 금지
- 모든 ML 모델은 Python에서 직접 학습
- ECS Fargate에서 학습 작업 실행
- Step Functions로 MLOps 제어
- 모델 버전별 S3 파일 보존
- 시간 순서 기반 데이터 분할
- Train/Validation/Test 데이터 누수 방지
- 신규 모델 배포 전 성능 검증
- 관리자 승인 및 배포 상태 점검
- 운영 장애 시 이전 모델로 롤백
- 초기 서비스는 조건 기반 시나리오 예측
