# syntax=docker/dockerfile:1

# --- Stage 1: Build Vite React/TypeScript client ---
FROM node:20-alpine AS client-builder
WORKDIR /client

COPY client/package.json client/package-lock.json ./
RUN npm ci

COPY client/ ./
RUN npm run build

# --- Stage 2: Single-worker Python 3.12 FastAPI + static client ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/var/data

WORKDIR /app

RUN groupadd --system appuser && useradd --system --gid appuser --create-home appuser \
    && mkdir -p /var/data /app/static && chown -R appuser:appuser /var/data /app

COPY pyproject.toml ./
COPY server/ ./server/
COPY voice_khata/ ./voice_khata/
COPY --from=client-builder /client/dist/ /app/static/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/healthz', timeout=3)" || exit 1

CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips=\"*\" --ws-ping-interval 20 --ws-ping-timeout 20"]
