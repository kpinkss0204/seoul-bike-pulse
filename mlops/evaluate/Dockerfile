FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg
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
COPY mlops/evaluate/evaluation_worker.py \
 ./evaluation_worker.py
ENTRYPOINT ["python", "-u", "/app/evaluation_worker.py"]
CMD ["--mode", "lock"]
