# ==============================================================================
# Stage 1: Build the Vite PWA Client
# ==============================================================================
FROM node:20-alpine AS builder

WORKDIR /app/client

# Install client dependencies using clean install
COPY client/package*.json ./
RUN npm ci

# Copy client source code and compile production assets to dist/
COPY client/ ./
RUN npm run build

# ==============================================================================
# Stage 2: Production Python 3.12 Runtime
# ==============================================================================
FROM python:3.12-slim

WORKDIR /app

# Set Python environment flags
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    STATIC_DIR=/app/static \
    DATA_DIR=/var/data

# Install essential system dependencies (if needed by sqlite/crypto)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install server Python requirements
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

# Copy server code
COPY server /app/server

# Copy compiled static frontend from Stage 1 into /app/static
COPY --from=builder /app/client/dist /app/static

# Create non-root user and ensure /var/data and /app are writable
RUN useradd -u 1000 -m appuser \
    && mkdir -p /var/data /app/data \
    && chown -R appuser:appuser /var/data /app

# Switch to non-root user
USER appuser

# Expose default port (Render will inject $PORT dynamically)
EXPOSE 8000

# Start Uvicorn with exactly 1 worker (mandatory for in-process session state)
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*' --ws-ping-interval 20 --ws-ping-timeout 20"]
