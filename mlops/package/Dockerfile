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
COPY mlops/evaluate/requirements-evaluation.txt \
 ./requirements-evaluation.txt
RUN pip install --no-cache-dir \
 -r requirements-evaluation.txt
COPY mlops/package/package_worker.py \
 ./package_worker.py
COPY mlops/package/verify_package.py \
 ./verify_package.py
ENTRYPOINT ["python", "-u", "/app/package_worker.py"]
