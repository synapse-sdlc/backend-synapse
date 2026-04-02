import asyncio
import json

import redis
from app.workers.celery_app import celery_app
from app.config import settings


def _publish_event(feature_id: str, event: dict):
    """Publish an event to Redis pub/sub for SSE streaming."""
    r = redis.from_url(settings.redis_url)
    r.publish(f"feature:{feature_id}", json.dumps(event))
    r.close()


@celery_app.task(bind=True, name="app.workers.tasks.agent_run_task", time_limit=300)
def agent_run_task(self, feature_id: str, user_message: str):
    """Run a single agent turn for a feature conversation."""
    _publish_event(feature_id, {"type": "thinking", "message": "Agent is processing..."})

    # TODO: wire up agent_service.run_agent_turn()
    # This will:
    # 1. Load feature + messages from DB
    # 2. Select skill based on phase
    # 3. Run agent_loop() with on_event callback that calls _publish_event
    # 4. Save new messages to DB
    # 5. Check for new artifacts, update feature phase
    # 6. Publish "done" event

    _publish_event(feature_id, {"type": "done", "message": "Agent turn complete"})


@celery_app.task(bind=True, name="app.workers.tasks.analyze_codebase_task", time_limit=600)
def analyze_codebase_task(self, project_id: str, repo_path: str):
    """Run full codebase analysis pipeline for a project."""
    # TODO: wire up project_service.analyze_codebase()
    # This will:
    # 1. Clone repo (or use local path)
    # 2. Run static_analyzer.analyze_directory()
    # 3. Chunk and embed
    # 4. Run KB Agent to generate architecture
    # 5. Save architecture artifact to DB
    # 6. Update project.analysis_status = "ready"
    pass
