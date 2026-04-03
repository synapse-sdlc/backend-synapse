"""Redis pub/sub event publishing utility.

Shared by Celery workers and API endpoints (e.g., webhook handlers).
"""
import json
import redis
from app.config import settings


def publish_feature_event(feature_id: str, event: dict):
    """Publish an event to a feature's SSE channel via Redis."""
    r = redis.from_url(settings.redis_url)
    r.publish(f"feature:{feature_id}", json.dumps(event))


def publish_project_event(project_id: str, event: dict):
    """Publish an event to a project's SSE channel via Redis."""
    r = redis.from_url(settings.redis_url)
    r.publish(f"project:{project_id}", json.dumps(event))
