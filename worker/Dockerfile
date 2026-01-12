FROM python:3.12-slim AS builder

WORKDIR /app

# Install build deps for psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# -----------------------------
# Final runtime image
# -----------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install only runtime libs
RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy worker source
COPY src/ /app/

# Create non-root user
RUN useradd -m workeruser
USER workeruser

CMD ["python", "worker.py"]