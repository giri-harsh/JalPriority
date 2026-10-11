FROM python:3.12-slim

WORKDIR /app

# Install dependencies and package
COPY backend /app/backend
RUN pip install --no-cache-dir -r /app/backend/requirements.txt && \
    pip install --no-cache-dir -e /app/backend

# Create persistent storage directory for SQLite
RUN mkdir -p /app/var
VOLUME ["/app/var"]

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    B_PORT=8002 \
    B_STORE_MODE=sqlite \
    B_SQLITE_PATH=/app/var/jp_b.sqlite3 \
    B_USE_MOCK_A=false \
    B_UPSTREAM_A_URL=http://jp-a-api:8001

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:' + str('${B_PORT}') + '/b/v1/health').getcode() == 200 else 1)"

CMD ["sh", "-c", "uvicorn jp_b.main:app --host 0.0.0.0 --port ${B_PORT}"]

