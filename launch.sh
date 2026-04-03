#!/bin/sh
set -e

echo "Starting Synapse Backend API..."

# Run pending migrations and start server
alembic upgrade head
exec envault run -e synapse "uvicorn app.main:app --host 0.0.0.0 --port 8000"
