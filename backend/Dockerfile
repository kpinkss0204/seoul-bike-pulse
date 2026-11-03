FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
 libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements-serving.txt \
 /app/requirements-serving.txt
RUN pip install --no-cache-dir \
 -r /app/requirements-serving.txt
COPY backend/app /app/app
COPY backend/healthcheck.py \
 /app/healthcheck.py
RUN useradd \
 --create-home \
 --uid 10001 \
 appuser
USER appuser
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
