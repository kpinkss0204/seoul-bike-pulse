FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY mlops/registry/requirements-registry.txt \
 ./requirements-registry.txt
RUN pip install --no-cache-dir \
 -r requirements-registry.txt
COPY mlops/registry/registry_worker.py \
 ./registry_worker.py
ENTRYPOINT ["python", "-u", "/app/registry_worker.py"]
CMD ["--action", "register"]
