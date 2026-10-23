FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg
WORKDIR /app
COPY mlops/eda/requirements-eda.txt \
 ./requirements-eda.txt
RUN pip install --no-cache-dir \
 -r requirements-eda.txt
COPY mlops/eda/eda_worker.py \
 ./eda_worker.py
CMD ["python", "-u", "/app/eda_worker.py"]
