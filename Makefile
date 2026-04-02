.PHONY: dev infra api worker test migrate

infra:
	docker compose up -d

api:
	PYTHONPATH=. uvicorn app.main:app --reload --port 8000

worker:
	PYTHONPATH=. celery -A app.workers.celery_app worker -l info -c 2

test:
	PYTHONPATH=. python -m pytest tests/ -v

migrate:
	PYTHONPATH=. alembic upgrade head

dev:
	@echo "Run in separate terminals:"
	@echo "  make infra   (postgres, redis, ollama)"
	@echo "  make api     (fastapi on :8000)"
	@echo "  make worker  (celery worker)"
