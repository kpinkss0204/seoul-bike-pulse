FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY mlops/ingestion/requirements-ingestion.txt \
 ./requirements-ingestion.txt
RUN pip install --no-cache-dir \
 -r requirements-ingestion.txt
COPY mlops/ingestion/ingestion_worker.py \
 ./ingestion_worker.py
ENTRYPOINT ["python", "-u", "/app/ingestion_worker.py"]
