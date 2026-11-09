FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r /tmp/requirements.txt
WORKDIR /app/ops
COPY ops/ /app/ops/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
