# SEOUL BIKE PULSE
AWS ECS + Step Functions 기반
서울 따릉이 대여량 예측 및 MLOps 플랫폼

## 프로젝트 목표
- 서울 따릉이 시간별 대여량 예측
- React 기반 수요 예측 대시보드
- Python 기반 머신러닝 모델 학습
- AWS Step Functions 기반 MLOps 자동화
- ECS Fargate 기반 서비스 배포
- 모델 모니터링 및 자동 재학습
- CI/CD 및 CloudFormation 자동화

## 기술 스택
### Frontend
- React
- TypeScript
- Vite
- Tailwind CSS (추후 적용)

### Backend
- Python 3.12
- FastAPI
- Uvicorn

### Machine Learning
- Scikit-learn
- Random Forest
- XGBoost

### AWS
- Amazon ECS
- AWS Fargate
- Amazon ECR
- Amazon S3
- AWS Step Functions
- Amazon DynamoDB
- AWS Glue
- Amazon Athena
- Amazon CloudWatch
- Amazon EventBridge
- AWS CodePipeline
- AWS CloudFormation

## 데이터셋
- Seoul Bike Sharing Demand (UCI Machine Learning Repository)
- Target: rented_bike_count

## Backend 실행
cd backend
source ../.venv/bin/activate
python -m uvicorn app.main:app \
 --host 127.0.0.1 \
 --port 8000 \
 --reload

## Frontend 실행
cd frontend
npm install
npm run dev

## Backend 테스트
cd backend
source ../.venv/bin/activate
python -m pytest -q

## 개발 주소
- Frontend: http://localhost:5173
- Backend: http://localhost:8000
- Swagger UI: http://localhost:8000/docs

## 설정 파일
- docs/STEP_A_REQUIREMENTS.md
- config/project_contract.json

## 개발 진행 상태
- [x] Step A: 요구사항 및 데이터 구조 확정
- [x] Step B: GitHub 및 로컬 개발 환경 구성
- [ ] Step C: AWS VPC 및 네트워크 구축
- [ ] Step D: IAM 역할 구성
- [ ] Step E: S3 데이터 레이크 구축
- [ ] Step F: ECR 및 ECS 실행 환경 구축

## 보안 정책
- AWS 인증 정보 GitHub 업로드 금지
- 환경 변수 파일 업로드 금지
- 학습 데이터와 모델 아티팩트 업로드 금지
- 관리자 기능 인증 및 권한 검사
- AWS AI 관리형 서비스 미사용
# seoul-bike-pulse
