FROM python:3.12-slim-bookworm
# To pin to an exact image digest replace the FROM line with a digest-pinned reference,
# e.g. `FROM python:3.12-slim-bookworm@sha256:<digest>`.
# Get the digest locally with: `docker pull python:3.12-slim-bookworm` then
# `docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim-bookworm`.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system deps for psycopg2 and PostgreSQL client tools (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user early with a fixed UID to make host volume permissions deterministic.
# Using UID 1000 for `appuser` (change if it conflicts on your host).
RUN useradd -u 1000 -m -s /usr/sbin/nologin appuser

# Copy and install Python dependencies
COPY requirements.txt /app/
RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy sources into explicit destinations to preserve directory structure
COPY src/ /app/src/
COPY templates/ /app/templates/
COPY static/ /app/static/
COPY locales/ /app/locales/

# Set ownership and drop to non-root user
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Production: run uvicorn directly
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["curl", "-fsS", "http://127.0.0.1:8000/health"]
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]