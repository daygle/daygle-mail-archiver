#!/bin/sh
set -e

echo "Startup diagnostic:"
echo "Working directory: $(pwd)"
echo "Listing /app:"
ls -la /app || true

echo "Listing /app/src:"
ls -la /app/src || true

echo "Python path:"
python -c "import sys; print(sys.path)"

exec uvicorn app:app --host 0.0.0.0 --port 8000
