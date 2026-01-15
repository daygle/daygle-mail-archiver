FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system deps for psycopg2 and PostgreSQL client tools
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m appuser

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ templates/ static/ locales/ /app/

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]