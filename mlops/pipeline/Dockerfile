FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY mlops/pipeline/requirements-gate.txt \
 ./requirements-gate.txt
RUN pip install --no-cache-dir \
 -r requirements-gate.txt
COPY mlops/pipeline/pipeline_gate.py \
 ./pipeline_gate.py
ENTRYPOINT ["python", "-u", "/app/pipeline_gate.py"]
CMD ["--gate", "raw"]
