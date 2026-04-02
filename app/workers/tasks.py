import asyncio
import json
import logging

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app
from app.config import settings, get_provider

logger = logging.getLogger(__name__)

# Sync engine for Celery tasks (Celery workers are synchronous)
_sync_engine = None


def _get_sync_session() -> Session:
    global _sync_engine
    if _sync_engine is None:
        sync_url = settings.database_url.replace("+asyncpg", "")
        _sync_engine = create_engine(sync_url)
    return Session(_sync_engine)


def _publish_event(channel: str, event: dict):
    """Publish an event to Redis pub/sub for SSE streaming."""
    r = redis.from_url(settings.redis_url)
    r.publish(channel, json.dumps(event))
    r.close()


def _publish_project_event(project_id: str, event: dict):
    _publish_event(f"project:{project_id}", event)


def _publish_feature_event(feature_id: str, event: dict):
    _publish_event(f"feature:{feature_id}", event)


@celery_app.task(bind=True, name="app.workers.tasks.agent_run_task", time_limit=300)
def agent_run_task(self, feature_id: str, user_message: str):
    """Run a single agent turn for a feature conversation."""
    _publish_feature_event(feature_id, {"type": "thinking", "message": "Agent is processing..."})

    # TODO: wire up agent_service.run_agent_turn()
    # This will:
    # 1. Load feature + messages from DB via _get_sync_session()
    # 2. Select skill based on phase
    # 3. Run agent_loop() with on_event callback that calls _publish_feature_event
    # 4. Save new messages to DB
    # 5. Check for new artifacts, update feature phase
    # 6. Publish "done" event

    _publish_feature_event(feature_id, {"type": "done", "message": "Agent turn complete"})


@celery_app.task(bind=True, name="app.workers.tasks.analyze_codebase_task", time_limit=600)
def analyze_codebase_task(self, project_id: str, github_url: str):
    """Full codebase analysis pipeline.

    Flow:
    1. git clone from GitHub to /tmp
    2. tar + upload to S3 (persistent storage)
    3. Run tree-sitter AST analysis on the local copy
    4. Chunk results, generate embeddings, index in vector store
    5. Build context summary for agent
    6. Run KB Agent (agent_loop with codebase-analysis skill) to generate architecture
    7. Save architecture artifact to DB
    8. Update project status to "ready"
    9. Cleanup local temp files
    """
    from app.services.project_service import (
        clone_repo_to_s3,
        download_repo_from_s3,
        build_context_summary,
        cleanup_local_repo,
    )
    from core.indexer.static_analyzer import analyze_directory
    from core.indexer.chunker import chunk_analysis_results
    from core.indexer.vector_store import VectorStore

    session = _get_sync_session()

    try:
        # Load project
        from app.models.project import Project
        project = session.get(Project, project_id)
        if not project:
            logger.error(f"Project {project_id} not found")
            return

        project.analysis_status = "analyzing"
        session.commit()
        _publish_project_event(project_id, {"type": "status", "status": "analyzing"})

        # Step 1: Clone to /tmp, upload to S3
        _publish_project_event(project_id, {"type": "step", "step": "Cloning repository..."})
        s3_key = clone_repo_to_s3(project_id, github_url)
        project.s3_repo_key = s3_key
        session.commit()

        # Step 2: Get local path (downloads from S3 if needed)
        local_repo_path = download_repo_from_s3(project_id, s3_key)
        _publish_project_event(project_id, {"type": "step", "step": "Repository cloned. Running analysis..."})

        # Step 3: Static analysis via tree-sitter
        _publish_project_event(project_id, {"type": "step", "step": "Running static analysis..."})
        analysis = analyze_directory(local_repo_path)
        logger.info(f"Analyzed {analysis['files_analyzed']} files")

        # Step 4: Chunk and index
        _publish_project_event(project_id, {"type": "step", "step": "Indexing codebase..."})
        chunks = chunk_analysis_results(analysis)
        store = VectorStore()
        store.add_chunks(chunks)
        logger.info(f"Indexed {len(chunks)} chunks")

        # Step 5: Build context summary
        codebase_context = build_context_summary(analysis, local_repo_path)
        project.codebase_context = codebase_context
        session.commit()

        # Step 6: Run KB Agent to generate architecture
        _publish_project_event(project_id, {"type": "step", "step": "Generating architecture overview..."})
        provider = get_provider()
        from core.orchestrator.loop import agent_loop
        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=f"Analyze the codebase at {local_repo_path}. Generate a complete architecture overview.",
            skill_name="codebase-analysis",
            codebase_context=codebase_context,
        ))
        logger.info(f"Architecture generated in {result['turns']} turns")

        # Step 7: Save architecture artifact to DB
        if result.get("artifact_id"):
            from app.models.artifact import Artifact
            import json as json_mod
            from pathlib import Path

            # Read the artifact from filesystem (the core tool wrote it there)
            artifact_path = Path("./artifacts") / f"{result['artifact_id']}.json"
            if artifact_path.exists():
                artifact_data = json_mod.loads(artifact_path.read_text())
                db_artifact = Artifact(
                    id=artifact_data["id"],
                    type=artifact_data["type"],
                    name=artifact_data["name"],
                    content=json_mod.loads(artifact_data["content"]) if isinstance(artifact_data["content"], str) else artifact_data["content"],
                    content_md=None,
                    parent_id=None,
                    status="approved",
                    version=1,
                    project_id=project_id,
                )
                session.merge(db_artifact)

        # Step 8: Mark project as ready
        project.analysis_status = "ready"
        session.commit()
        _publish_project_event(project_id, {"type": "status", "status": "ready"})

        # Step 9: Cleanup (optional, keep cache for demo)
        # cleanup_local_repo(project_id)

        logger.info(f"Project {project_id} analysis complete")

    except Exception as e:
        logger.exception(f"Codebase analysis failed for project {project_id}")
        project.analysis_status = "failed"
        session.commit()
        _publish_project_event(project_id, {"type": "error", "message": str(e)})
    finally:
        session.close()
