FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY mlops/requirements-runtime.txt ./requirements.txt
RUN pip install --no-cache-dir \
 -r requirements.txt
COPY mlops/data/quality_worker.py ./quality_worker.py
COPY config/project_contract.json \
 ./config/project_contract.json
CMD ["python", "-u", "/app/quality_worker.py"]
