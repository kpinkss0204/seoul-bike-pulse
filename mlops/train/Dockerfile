FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
WORKDIR /app
COPY mlops/train/requirements-train.txt \
 ./requirements-train.txt
RUN pip install --no-cache-dir \
 -r requirements-train.txt
COPY mlops/train/training_worker.py \
 ./training_worker.py
CMD ["python", "-u", "/app/training_worker.py"]
