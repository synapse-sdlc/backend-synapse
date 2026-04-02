.PHONY: dev infra api worker test migrate makemigrations db-current

infra:
	docker compose up -d

api:
	PYTHONPATH=. uvicorn app.main:app --reload --port 8000

worker:
	PYTHONPATH=. celery -A app.workers.celery_app worker -l info -c 2

test:
	PYTHONPATH=. python -m pytest tests/ -v

# Like Django's "migrate" — applies pending migrations
migrate:
	PYTHONPATH=. alembic upgrade head

makemigrations:
	PYTHONPATH=. alembic revision --autogenerate -m "$(msg)"

db-current:
	PYTHONPATH=. alembic current

dev:
	@echo "Run in separate terminals:"
	@echo "  make infra          (postgres, redis, ollama)"
	@echo "  make api            (fastapi on :8000)"
	@echo "  make worker         (celery worker)"
	@echo ""
	@echo "Migrations (like Django):"
	@echo "  make makemigrations msg='add priority column'"
	@echo "  make migrate"
	@echo "  make db-current"
