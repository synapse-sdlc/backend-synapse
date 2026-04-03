#!/bin/sh
set -e

echo "Starting Celery..."

# Run application with envault to inject secrets
exec envault run -e assetguard "celery -A app.workers.celery_app worker -l info -c 2"
