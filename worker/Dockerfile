# To pin to a specific base image digest, replace the FROM lines with a digest-pinned reference,
# for example: `FROM python:3.12-slim-bookworm@sha256:<digest>`.
# Locally you can fetch the digest with `docker pull python:3.12-slim-bookworm` then
# `docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim-bookworm`.
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# Install build deps for psycopg2 (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt


# -----------------------------
# Final runtime image
# -----------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install only runtime libs (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy worker source
COPY src/ /app/

# Create non-root user with fixed UID and fix permissions (use 1001 to avoid colliding with host 1000)
RUN useradd -u 1001 -m -s /usr/sbin/nologin workeruser && chown -R workeruser:workeruser /app
USER workeruser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["sh", "-c", "ps aux | grep -v grep | grep worker.py >/dev/null"]

CMD ["python", "worker.py"]