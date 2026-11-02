FROM python:3.12-slim
ARG ROLE_KIND=processor
ENV ROLE_KIND=${ROLE_KIND}
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY mlops/requirements-runtime.txt \
 ./requirements.txt
RUN pip install --no-cache-dir \
 -r requirements.txt
COPY mlops/runtime/smoke.py \
 ./smoke.py
CMD ["python", "-u", "/app/smoke.py"]
