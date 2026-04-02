from celery import Celery
from app.config import settings

celery_app = Celery(
    "synapse",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue="synapse-agents",
    task_routes={
        "app.workers.tasks.analyze_codebase_task": {"queue": "synapse-heavy"},
        "app.workers.tasks.agent_run_task": {"queue": "synapse-agents"},
    },
)

celery_app.autodiscover_tasks(["app.workers"])
