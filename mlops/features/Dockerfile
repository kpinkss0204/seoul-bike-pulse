FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY mlops/features/requirements-features.txt \
 ./requirements-features.txt
RUN pip install --no-cache-dir \
 -r requirements-features.txt
COPY mlops/features/feature_worker.py \
 ./feature_worker.py
COPY config/feature_split_contract.json \
 ./config/feature_split_contract.json
CMD ["python", "-u", "/app/feature_worker.py"]
