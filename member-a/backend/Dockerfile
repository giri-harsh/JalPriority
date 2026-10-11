# JalPriority AI - Member A API image.
# Build context is the member-a directory:   docker build -f backend/Dockerfile -t jp-a-api .
# Run:                                       docker run --rm -p 8001:8001 jp-a-api
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    A_DATA_DIR=/app/data \
    A_MODELS_DIR=/app/models \
    A_PORT=8001 \
    SHARED_ENV=docker

# libgomp1: OpenMP runtime that the xgboost wheel links against (not part of the slim image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so the layer is cached until requirements.txt changes.
COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Application code, seed data (data/derived is excluded by .dockerignore) and the trained model.
COPY backend/jp_a /app/jp_a
COPY data /app/data
COPY models /app/models

# Non-root user; derived scenarios (disruptions) are written under data/derived at runtime.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin jpa \
    && mkdir -p /app/data/derived \
    && chown -R jpa:jpa /app/data

USER jpa
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/a/v1/health' % os.environ.get('A_PORT', '8001'), timeout=4)"]

# sh -c expands ${A_PORT}; exec keeps uvicorn as PID 1 for clean signal handling.
CMD ["sh", "-c", "exec uvicorn jp_a.main:app --host 0.0.0.0 --port ${A_PORT:-8001}"]
