from celery import Celery
from app.config import settings

celery_app = Celery(
    "synapse",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

if settings.sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[
            sentry_sdk.integrations.celery.CeleryIntegration(monitor_beat_tasks=True),
            sentry_sdk.integrations.sqlalchemy.SqlalchemyIntegration(),
        ],
        send_default_pii=False,
    )

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue="synapse-agents",
    # Don't acknowledge tasks until they complete — prevents loss on worker crash/restart
    task_acks_late=True,
    # Reject and requeue tasks if worker is killed mid-execution
    task_reject_on_worker_lost=True,
    # Only prefetch 1 task per worker (don't grab tasks you can't run yet)
    worker_prefetch_multiplier=1,
)

celery_app.autodiscover_tasks(["app.workers"])
