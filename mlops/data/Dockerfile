FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY mlops/requirements-runtime.txt \
 ./requirements-runtime.txt
COPY mlops/data/requirements-transform.txt \
 ./requirements-transform.txt
RUN pip install --no-cache-dir \
 -r requirements-runtime.txt \
 -r requirements-transform.txt
COPY mlops/data/transform_worker.py \
 ./transform_worker.py
COPY config/project_contract.json \
 ./config/project_contract.json
CMD ["python", "-u", "/app/transform_worker.py"]
