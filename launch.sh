#!/bin/sh
set -e

echo "Starting Synapse Backend API..."

# Run application with envault to inject secrets
alembic revision --autogenerate -m "$(msg)"
alembic upgrade head
exec envault run -e assetguard "uvicorn app.main:app --host 0.0.0.0 --port 8000"
