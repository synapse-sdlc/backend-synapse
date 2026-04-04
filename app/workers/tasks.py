import asyncio
import json
import logging
import re
import time
from typing import Optional

import redis
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app
from app.config import settings, get_provider

# Import all models so SQLAlchemy metadata resolves foreign keys
import app.models.org  # noqa: F401
import app.models.user  # noqa: F401
import app.models.project  # noqa: F401
import app.models.feature  # noqa: F401
import app.models.artifact  # noqa: F401
import app.models.message  # noqa: F401
import app.models.repository  # noqa: F401
import app.models.jira_config  # noqa: F401
import app.models.jira_issue_link  # noqa: F401
import app.models.pr_link  # noqa: F401
import app.models.knowledge_entry  # noqa: F401
import app.models.api_contract  # noqa: F401
import app.models.shared_model  # noqa: F401

logger = logging.getLogger(__name__)

# Sync engine for Celery tasks (Celery workers are synchronous)
_sync_engine = None


def _get_sync_session() -> Session:
    global _sync_engine
    if _sync_engine is None:
        sync_url = settings.database_url.replace("+asyncpg", "")
        ssl_mode = "disable" if "localhost" in sync_url else "require"
        _sync_engine = create_engine(
            sync_url, connect_args={"sslmode": ssl_mode})
    return Session(_sync_engine)


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    msg = str(exc).lower()
    return any(k in msg for k in ("timeout", "connection", "429", "503", "throttl", "rate limit"))


_redis_pool = None


def _get_redis():
    """Get a Redis connection from a shared pool (avoids per-event connection churn)."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(settings.redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def _publish_event(channel: str, event: dict):
    """Publish an event to Redis pub/sub for SSE streaming."""
    r = _get_redis()
    r.publish(channel, json.dumps(event))


def _publish_project_event(project_id: str, event: dict):
    _publish_event(f"project:{project_id}", event)


def _publish_feature_event(feature_id: str, event: dict):
    """Publish to feature SSE channel. Uses worker's pooled Redis for efficiency."""
    _publish_event(f"feature:{feature_id}", event)


def _estimate_hours_saved(artifact_type, content):
    """Conservative estimate of manual hours this artifact would take."""
    if not isinstance(content, dict):
        return 0.5
    if artifact_type == "spec":
        stories = len(content.get("user_stories", []))
        return 1.0 + stories * 0.5
    elif artifact_type == "plan":
        subtasks = len(content.get("subtasks", []))
        return 2.0 + subtasks * 0.3
    elif artifact_type == "tests":
        cases = sum(len(s.get("test_cases", []))
                    for s in content.get("test_suites", []))
        return 1.5 + cases * 0.15
    return 0.5


def _update_feature_metrics(session, feature_id, result, task_start):
    """Update cost/time tracking metrics on a feature after an agent run."""
    try:
        from app.models.feature import Feature
        from app.models.artifact import Artifact
        feature = session.get(Feature, feature_id)
        if not feature:
            return
        duration_ms = int((time.monotonic() - task_start) * 1000)
        feature.total_turns = (feature.total_turns or 0) + \
            result.get("turns", 0)
        feature.total_duration_ms = (
            feature.total_duration_ms or 0) + duration_ms

        # Estimate hours saved based on artifact
        artifact_id = result.get("artifact_id")
        if artifact_id:
            artifact = session.get(Artifact, artifact_id)
            if artifact and artifact.content:
                feature.estimated_hours_saved = (
                    feature.estimated_hours_saved or 0.0) + _estimate_hours_saved(artifact.type, artifact.content)
        session.commit()
        logger.info(
            f"Feature {feature_id} metrics: +{result.get('turns', 0)} turns, +{duration_ms}ms, total_hours_saved={feature.estimated_hours_saved:.1f}")
    except Exception as e:
        logger.warning(f"Failed to update feature metrics: {e}")


@celery_app.task(bind=True, name="app.workers.tasks.agent_run_task", time_limit=600, max_retries=1)
def agent_run_task(self, feature_id: str, user_message: str, model_tier: str = "balanced"):
    """Run a single agent turn for a feature conversation."""
    session = _get_sync_session()
    lock = None
    try:
        # Redis distributed lock — prevents concurrent agent runs on same feature
        r = _get_redis()
        lock = r.lock(f"agent:{feature_id}", timeout=600, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            logger.warning(
                f"Agent lock held for feature {feature_id}, skipping")
            return {"skipped": True, "reason": "agent_locked"}

        from app.models.feature import Feature
        feature = session.get(Feature, feature_id)
        if not feature:
            logger.warning(
                f"Feature {feature_id} not found, skipping stale task")
            return {"skipped": True, "reason": "feature_not_found"}

        _publish_feature_event(
            feature_id, {"type": "thinking", "message": "Agent is processing..."})

        def on_event(event):
            _publish_feature_event(feature_id, event)

        task_start = time.monotonic()

        from app.services.agent_service import run_agent_turn
        result = asyncio.run(run_agent_turn(
            feature_id=feature_id,
            user_message=user_message,
            db=session,
            on_event=on_event,
            model_tier=model_tier,
        ))
        # Flush Langfuse buffer before the task returns (worker won't shutdown between tasks)
        from core.orchestrator.tracing import flush as _lf_flush
        _lf_flush(blocking=True)

        # Track cost/time metrics on feature
        _update_feature_metrics(session, feature_id, result, task_start)

        _publish_feature_event(feature_id, {
            "type": "response",
            "content": result["final_response"],
            "phase": result["phase"],
            "artifact_id": result.get("artifact_id"),
        })
        _publish_feature_event(
            feature_id, {"type": "done", "phase": result["phase"]})

        return result

    except Exception as e:
        logger.exception(f"Agent turn failed for feature {feature_id}")
        _publish_feature_event(
            feature_id, {"type": "error", "message": str(e)})
        # Retry once on transient errors
        if self.request.retries < self.max_retries and _is_retryable(e):
            raise self.retry(countdown=10, exc=e)
        raise
    finally:
        # Clear agent_task_id
        try:
            from app.models.feature import Feature
            feature = session.get(Feature, feature_id)
            if feature and feature.agent_task_id == self.request.id:
                feature.agent_task_id = None
                session.commit()
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
        if lock and lock.owned():
            lock.release()
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.approval_agent_task", time_limit=600, max_retries=1)
def approval_agent_task(self, feature_id: str, model_tier: str = "balanced"):
    """Run the next agent after an approval (plan or QA generation)."""
    session = _get_sync_session()
    lock = None
    try:
        r = _get_redis()
        lock = r.lock(f"agent:{feature_id}", timeout=600, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            logger.warning(
                f"Agent lock held for feature {feature_id}, skipping approval")
            return {"skipped": True, "reason": "agent_locked"}

        from app.models.feature import Feature
        feature = session.get(Feature, feature_id)
        if not feature:
            logger.warning(
                f"Feature {feature_id} not found, skipping stale approval task")
            return {"skipped": True, "reason": "feature_not_found"}

        _publish_feature_event(
            feature_id, {"type": "thinking", "message": "Generating next artifact..."})

        def on_event(event):
            _publish_feature_event(feature_id, event)

        task_start = time.monotonic()

        from app.services.agent_service import run_approval_agent
        result = asyncio.run(run_approval_agent(
            feature_id=feature_id,
            db=session,
            on_event=on_event,
            model_tier=model_tier,
        ))
        from core.orchestrator.tracing import flush as _lf_flush
        _lf_flush(blocking=True)

        # Track cost/time metrics on feature
        _update_feature_metrics(session, feature_id, result, task_start)

        _publish_feature_event(feature_id, {
            "type": "response",
            "content": result.get("final_response", ""),
            "phase": result["phase"],
            "artifact_id": result.get("artifact_id"),
        })
        _publish_feature_event(
            feature_id, {"type": "done", "phase": result["phase"]})

        return result

    except Exception as e:
        logger.exception(f"Approval agent failed for feature {feature_id}")
        _publish_feature_event(
            feature_id, {"type": "error", "message": str(e)})
        if self.request.retries < self.max_retries and _is_retryable(e):
            raise self.retry(countdown=10, exc=e)
        raise
    finally:
        try:
            from app.models.feature import Feature
            feature = session.get(Feature, feature_id)
            if feature and feature.agent_task_id == self.request.id:
                feature.agent_task_id = None
                session.commit()
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
        if lock and lock.owned():
            lock.release()
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.scaffold_generation_task", time_limit=600, max_retries=1)
def scaffold_generation_task(self, feature_id: str, model_tier: str = "balanced"):
    """Generate code scaffolds from approved plan + spec + tests."""
    session = _get_sync_session()
    lock = None
    try:
        r = _get_redis()
        lock = r.lock(f"agent:{feature_id}", timeout=600, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            logger.warning(
                f"Agent lock held for feature {feature_id}, skipping scaffold")
            return {"skipped": True, "reason": "agent_locked"}

        from app.models.feature import Feature
        feature = session.get(Feature, feature_id)
        if not feature:
            return {"skipped": True, "reason": "feature_not_found"}

        _publish_feature_event(
            feature_id, {"type": "thinking", "message": "Generating code scaffolds..."})

        def on_event(event):
            _publish_feature_event(feature_id, event)

        task_start = time.monotonic()

        from app.services.agent_service import run_scaffold_agent
        result = asyncio.run(run_scaffold_agent(
            feature_id=feature_id,
            db=session,
            on_event=on_event,
            model_tier=model_tier,
        ))

        _update_feature_metrics(session, feature_id, result, task_start)

        _publish_feature_event(feature_id, {
            "type": "response",
            "content": result.get("final_response", ""),
            "phase": result["phase"],
            "artifact_id": result.get("artifact_id"),
        })
        _publish_feature_event(
            feature_id, {"type": "done", "phase": result["phase"]})

        return result

    except Exception as e:
        logger.exception(
            f"Scaffold generation failed for feature {feature_id}")
        _publish_feature_event(
            feature_id, {"type": "error", "message": str(e)})
        if self.request.retries < self.max_retries and _is_retryable(e):
            raise self.retry(countdown=10, exc=e)
        raise
    finally:
        try:
            from app.models.feature import Feature
            feature = session.get(Feature, feature_id)
            if feature and feature.agent_task_id == self.request.id:
                feature.agent_task_id = None
                session.commit()
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
        if lock and lock.owned():
            lock.release()
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.analyze_repository_task", time_limit=600)
def analyze_repository_task(self, repository_id: str):
    """Per-repository codebase analysis pipeline.

    Flow:
    1. Clone from GitHub to /tmp
    2. Upload to S3 as repos/{project_id}/{repo_id}/repo.tar.gz
    3. Run tree-sitter AST analysis
    4. Chunk + index into per-repo vector collection
    5. Build codebase context summary
    6. Run KB Agent to generate per-repo architecture
    7. Save architecture artifact to DB
    8. Mark repo as "ready"
    9. Check if all repos in project are ready -> update project status
    """
    from app.services.project_service import (
        clone_repo_to_s3,
        download_repo_from_s3,
        build_context_summary,
    )
    from core.indexer.static_analyzer import analyze_directory
    from core.indexer.chunker import chunk_analysis_results
    from core.indexer.vector_store import VectorStore

    session = _get_sync_session()

    try:
        from app.models.repository import Repository
        from app.models.project import Project
        from app.models.artifact import Artifact

        repo = session.get(Repository, repository_id)
        if not repo:
            logger.error(f"Repository {repository_id} not found")
            return

        project = session.get(Project, repo.project_id)
        project_id = str(repo.project_id)
        repo_id = str(repo.id)

        repo.analysis_status = "analyzing"
        session.commit()
        _publish_project_event(project_id, {
            "type": "status", "status": "analyzing",
            "repo_id": repo_id, "repo_name": repo.name,
        })

        # Decrypt GitHub token
        github_token = None
        if repo.github_token_encrypted:
            from app.utils.crypto import decrypt_token
            github_token = decrypt_token(repo.github_token_encrypted)
        elif project and project.github_token_encrypted:
            from app.utils.crypto import decrypt_token
            github_token = decrypt_token(project.github_token_encrypted)

        # Step 1: Clone to S3 (repo-scoped key)
        _publish_project_event(project_id, {
            "type": "step", "step": f"Cloning {repo.name}...", "repo_name": repo.name,
        })
        s3_key = clone_repo_to_s3(
            project_id=project_id,
            github_url=repo.github_url,
            github_token=github_token,
            repo_id=repo_id,
        )
        repo.s3_repo_key = s3_key
        session.commit()

        # Step 2: Get local path
        local_repo_path = download_repo_from_s3(
            project_id, s3_key, repo_id=repo_id)
        _publish_project_event(project_id, {
            "type": "step", "step": f"{repo.name} cloned. Running analysis...", "repo_name": repo.name,
        })

        # Step 3: Static analysis via tree-sitter
        analysis = analyze_directory(local_repo_path)
        logger.info(
            f"Analyzed {analysis['files_analyzed']} files for repo {repo.name}")

        # Step 4: Chunk and index (repo-scoped collection)
        _publish_project_event(project_id, {
            "type": "step", "step": f"Indexing {repo.name}...", "repo_name": repo.name,
        })
        chunks = chunk_analysis_results(analysis)
        store = VectorStore()
        store.add_chunks_to_repo(project_id, repo_id, chunks)
        logger.info(f"Indexed {len(chunks)} chunks for repo {repo.name}")

        # Step 5: Build context summary
        codebase_context = build_context_summary(analysis, local_repo_path)
        repo.codebase_context = codebase_context
        session.commit()
        logger.info(f"Codebase context saved for repo {repo.name}: {len(codebase_context or '')} chars")

        # Step 6: Run agent to generate per-repo architecture
        _publish_project_event(project_id, {
            "type": "step", "step": f"Generating architecture for {repo.name}...", "repo_name": repo.name,
        })

        # Check if project has uploaded architecture to use as base
        uploaded_arch_context = ""
        if project and project.uploaded_architecture_id:
            uploaded_art = session.get(
                Artifact, project.uploaded_architecture_id)
            if uploaded_art:
                arch_content = uploaded_art.content
                if isinstance(arch_content, dict):
                    uploaded_arch_context = (
                        "\n\n## Existing Architecture Document (provided by team)\n"
                        "Use this as the BASE and ENHANCE it with your analysis findings. "
                        "Do NOT discard this information — merge your discoveries into it.\n\n"
                        + json.dumps(arch_content, indent=2)
                    )
                elif isinstance(arch_content, str):
                    uploaded_arch_context = (
                        "\n\n## Existing Architecture Document (provided by team)\n"
                        + arch_content
                    )

        # Set artifact context for project-scoped storage
        from core.tools.artifacts.store_artifact import StoreArtifactTool
        from core.tools.artifacts.get_artifact import GetArtifactTool
        StoreArtifactTool.set_context(project_id=project_id)
        GetArtifactTool.set_context(project_id=project_id)

        # Set sandbox to allow agent to read the cloned repo + artifacts
        from core.tools.sandbox import set_sandbox
        from pathlib import Path as _SPath
        set_sandbox([
            str(local_repo_path),
            str((_SPath("./artifacts") / project_id).resolve()),
            str(_SPath("./artifacts").resolve()),
        ])

        provider = get_provider()
        from core.orchestrator.loop import agent_loop

        user_message = f"Analyze the codebase at {local_repo_path}. Generate a complete architecture overview."
        if uploaded_arch_context:
            user_message += (
                "\n\nIMPORTANT: A team-provided architecture document exists. "
                "Your output MUST incorporate and enhance this document, not replace it."
            )

        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=user_message,
            skill_name="codebase-analysis",
            codebase_context=codebase_context + uploaded_arch_context,
            # Observability: use repo_id as session so all analysis turns are grouped
            trace_session_id=repo_id,
            trace_metadata={
                "repo_id": repo_id,
                "project_id": project_id,
                "repo_name": repo.name,
                "task": "analyze_repository",
            },
        ))
        from core.orchestrator.tracing import flush as _lf_flush
        _lf_flush(blocking=True)
        logger.info(
            f"Architecture generated for {repo.name} in {result['turns']} turns")

        # Step 7: Save architecture artifact to DB
        artifact_id = result.get("artifact_id")
        logger.info(f"Agent loop returned artifact_id={artifact_id} for repo {repo.name}")

        if artifact_id:
            from pathlib import Path

            # Check project-scoped dir first, flat fallback
            artifact_path = Path("./artifacts") / project_id / f"{artifact_id}.json"
            if not artifact_path.exists():
                artifact_path = Path("./artifacts") / f"{artifact_id}.json"
                logger.info(f"Artifact not in scoped dir, trying flat: {artifact_path}")

            if artifact_path.exists():
                artifact_data = json.loads(artifact_path.read_text())
                content = artifact_data.get("content", "")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        content = {"raw": content}

                db_artifact = Artifact(
                    id=artifact_data["id"],
                    type=artifact_data["type"],
                    name=artifact_data.get(
                        "name", f"Architecture: {repo.name}"),
                    content=content,
                    content_md=None,
                    parent_id=None,
                    status="approved",
                    version=1,
                    project_id=project_id,
                    repo_id=repo.id,
                )
                session.merge(db_artifact)
                logger.info(f"Architecture artifact {artifact_id} saved to DB for repo {repo.name}")
            else:
                logger.error(f"Architecture artifact file NOT FOUND at {artifact_path} — artifact will be missing from DB")
        else:
            logger.warning(f"Agent loop returned no artifact_id for repo {repo.name} — architecture not generated")

        # Step 8: Mark repo as ready
        repo.analysis_status = "ready"
        session.commit()
        _publish_project_event(project_id, {
            "type": "status", "status": "ready",
            "repo_id": repo_id, "repo_name": repo.name,
        })

        # Step 9: Check if all repos in project are ready → trigger synthesis
        all_repos = session.execute(
            select(Repository).where(Repository.project_id == repo.project_id)
        ).scalars().all()
        if all(r.analysis_status == "ready" for r in all_repos):
            if project:
                project.analysis_status = "ready"
                session.commit()
            _publish_project_event(
                project_id, {"type": "status", "status": "ready", "all_repos": True})
            # Trigger unified project architecture synthesis if multi-repo
            if len(all_repos) >= 2:
                synthesize_project_task.delay(project_id)

        logger.info(
            f"Repository {repo.name} ({repository_id}) analysis complete")

    except Exception as e:
        logger.exception(f"Repository analysis failed for {repository_id}")
        try:
            repo = session.get(Repository, repository_id)
            if repo:
                repo.analysis_status = "failed"
                session.commit()
                _publish_project_event(str(repo.project_id), {
                    "type": "error", "message": str(e),
                    "repo_id": repository_id, "repo_name": repo.name,
                })
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.analyze_codebase_task", time_limit=600)
def analyze_codebase_task(self, project_id: str, github_url: str):
    """Legacy: Full codebase analysis for single-repo projects.

    Kept for backward compatibility. New projects use analyze_repository_task.
    """
    from app.services.project_service import (
        clone_repo_to_s3,
        download_repo_from_s3,
        build_context_summary,
    )
    from core.indexer.static_analyzer import analyze_directory
    from core.indexer.chunker import chunk_analysis_results
    from core.indexer.vector_store import VectorStore

    session = _get_sync_session()

    try:
        from app.models.project import Project
        project = session.get(Project, project_id)
        if not project:
            logger.error(f"Project {project_id} not found")
            return

        project.analysis_status = "analyzing"
        session.commit()
        _publish_project_event(
            project_id, {"type": "status", "status": "analyzing"})

        github_token = None
        if project.github_token_encrypted:
            from app.utils.crypto import decrypt_token
            github_token = decrypt_token(project.github_token_encrypted)

        _publish_project_event(
            project_id, {"type": "step", "step": "Cloning repository..."})
        s3_key = clone_repo_to_s3(
            project_id, github_url, github_token=github_token)
        project.s3_repo_key = s3_key
        session.commit()

        local_repo_path = download_repo_from_s3(project_id, s3_key)
        _publish_project_event(
            project_id, {"type": "step", "step": "Repository cloned. Running analysis..."})

        _publish_project_event(
            project_id, {"type": "step", "step": "Running static analysis..."})
        analysis = analyze_directory(local_repo_path)
        logger.info(f"Analyzed {analysis['files_analyzed']} files")

        _publish_project_event(
            project_id, {"type": "step", "step": "Indexing codebase..."})
        chunks = chunk_analysis_results(analysis)
        store = VectorStore()
        store.add_chunks(chunks)
        logger.info(f"Indexed {len(chunks)} chunks into default collection for project {project_id[:8]}")

        codebase_context = build_context_summary(analysis, local_repo_path)
        project.codebase_context = codebase_context
        session.commit()

        _publish_project_event(
            project_id, {"type": "step", "step": "Generating architecture overview..."})

        # Set artifact context + sandbox for project-scoped storage
        from core.tools.artifacts.store_artifact import StoreArtifactTool
        from core.tools.artifacts.get_artifact import GetArtifactTool
        StoreArtifactTool.set_context(project_id=project_id)
        GetArtifactTool.set_context(project_id=project_id)

        from core.tools.sandbox import set_sandbox
        from pathlib import Path as _SPath
        set_sandbox([
            str(local_repo_path),
            str((_SPath("./artifacts") / project_id).resolve()),
            str(_SPath("./artifacts").resolve()),
        ])

        provider = get_provider()
        from core.orchestrator.loop import agent_loop
        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=f"Analyze the codebase at {local_repo_path}. Generate a complete architecture overview.",
            skill_name="codebase-analysis",
            codebase_context=codebase_context,
        ))
        logger.info(f"Architecture generated in {result['turns']} turns")

        if result.get("artifact_id"):
            from app.models.artifact import Artifact
            from pathlib import Path

            artifact_path = Path("./artifacts") / project_id / f"{result['artifact_id']}.json"
            if not artifact_path.exists():
                artifact_path = Path("./artifacts") / f"{result['artifact_id']}.json"
            if artifact_path.exists():
                artifact_data = json.loads(artifact_path.read_text())
                db_artifact = Artifact(
                    id=artifact_data["id"],
                    type=artifact_data["type"],
                    name=artifact_data["name"],
                    content=json.loads(artifact_data["content"]) if isinstance(
                        artifact_data["content"], str) else artifact_data["content"],
                    content_md=None,
                    parent_id=None,
                    status="approved",
                    version=1,
                    project_id=project_id,
                )
                session.merge(db_artifact)

        project.analysis_status = "ready"
        session.commit()
        _publish_project_event(
            project_id, {"type": "status", "status": "ready"})

        logger.info(f"Project {project_id} analysis complete")

    except Exception as e:
        logger.exception(f"Codebase analysis failed for project {project_id}")
        project.analysis_status = "failed"
        session.commit()
        _publish_project_event(
            project_id, {"type": "error", "message": str(e)})
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.kb_update_task", time_limit=600, max_retries=0)
def kb_update_task(self, feature_id: str):
    """Generate a KB entry from completed feature artifacts."""
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        feature = session.get(Feature, feature_id)
        if not feature:
            logger.warning(f"Feature {feature_id} not found for KB update")
            return

        artifact_ids = []
        if feature.spec_artifact_id:
            artifact_ids.append(f"spec: {feature.spec_artifact_id}")
        if feature.plan_artifact_id:
            artifact_ids.append(f"plan: {feature.plan_artifact_id}")
        if feature.tests_artifact_id:
            artifact_ids.append(f"tests: {feature.tests_artifact_id}")

        if not artifact_ids:
            logger.warning(
                f"No artifacts found for feature {feature_id}, skipping KB update")
            return

        _publish_feature_event(
            feature_id, {"type": "thinking", "message": "Generating knowledge base entry..."})

        # Set artifact context for project-scoped storage
        from core.tools.artifacts.store_artifact import StoreArtifactTool
        from core.tools.artifacts.get_artifact import GetArtifactTool
        StoreArtifactTool.set_context(project_id=str(feature.project_id))
        GetArtifactTool.set_context(project_id=str(feature.project_id))

        provider = get_provider()
        from core.orchestrator.loop import agent_loop

        kb_message = (
            f"Feature: {feature.description}\n\n"
            f"Artifact IDs:\n" +
            "\n".join(f"- {aid}" for aid in artifact_ids) + "\n\n"
            f"Read each artifact using get_artifact, then generate a KB entry "
            f"following the kb-update skill instructions."
        )

        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=kb_message,
            skill_name="kb-update",
        ))
        logger.info(
            f"KB update completed for feature {feature_id} in {result['turns']} turns")

        if result.get("artifact_id"):
            from app.models.artifact import Artifact
            from pathlib import Path

            artifact_path = Path("./artifacts") / str(feature.project_id) / f"{result['artifact_id']}.json"
            if not artifact_path.exists():
                artifact_path = Path("./artifacts") / f"{result['artifact_id']}.json"
            if artifact_path.exists():
                artifact_data = json.loads(artifact_path.read_text())
                content = artifact_data.get("content", "")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        content = {"raw": content}

                db_artifact = Artifact(
                    id=artifact_data["id"],
                    type="kb",
                    name=artifact_data.get(
                        "name", f"KB: {feature.description}"),
                    content=content,
                    parent_id=feature.tests_artifact_id or feature.plan_artifact_id or feature.spec_artifact_id,
                    status="approved",
                    version=1,
                    project_id=feature.project_id,
                )
                session.merge(db_artifact)
                session.commit()

                # Chain: accumulate knowledge entries from this KB artifact
                kb_accumulate_task.delay(feature_id, artifact_data["id"])

        _publish_feature_event(feature_id, {"type": "done"})

    except Exception as e:
        logger.exception(f"KB update failed for feature {feature_id}")
    finally:
        session.close()


def _build_epic_description(spec, plan, tests):
    """Rich epic description for PMs — business context, scope, and metrics."""
    lines = []
    if spec.get("business_context"):
        lines.append("## Business Context")
        lines.append(spec["business_context"])
        lines.append("")

    # Scope summary
    stories = spec.get("user_stories", [])
    subtasks = plan.get("subtasks", [])
    total_hours = sum(s.get("estimated_hours", 0) for s in subtasks)
    suites = tests.get("test_suites", [])
    total_tests = sum(len(s.get("test_cases", [])) for s in suites)

    lines.append("## Scope")
    lines.append(f"- **{len(stories)}** user stories")
    lines.append(
        f"- **{len(subtasks)}** implementation tasks ({total_hours}h estimated)")
    lines.append(f"- **{total_tests}** test cases across {len(suites)} suites")
    lines.append("")

    if spec.get("personas"):
        lines.append("## Target Users")
        for p in spec["personas"]:
            if isinstance(p, dict):
                lines.append(
                    f"- **{p.get('name', '')}**: {p.get('description', '')}")
        lines.append("")

    if spec.get("success_metrics"):
        lines.append("## Success Metrics")
        for m in spec["success_metrics"]:
            lines.append(f"- {m}")
        lines.append("")

    if spec.get("dependencies"):
        lines.append("## Dependencies")
        for d in spec["dependencies"]:
            lines.append(f"- {d}")
        lines.append("")

    if spec.get("out_of_scope"):
        lines.append("## Out of Scope")
        for o in spec["out_of_scope"]:
            lines.append(f"- {o}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Synapse AI SDLC Platform*")
    return "\n".join(lines)


def _build_story_description(story, spec):
    """Rich story description for PMs and devs — full user story with ACs and context."""
    lines = []
    # User story format
    lines.append("## User Story")
    lines.append(
        f"**As a** {story.get('role', '...')}, **I want** {story.get('action', '...')}, **so that** {story.get('benefit', '...')}.")
    lines.append("")

    # Acceptance criteria
    criteria = story.get("acceptance_criteria", [])
    if criteria:
        lines.append("## Acceptance Criteria")
        for i, ac in enumerate(criteria, 1):
            if isinstance(ac, dict):
                lines.append(f"### AC-{i}")
                lines.append(f"- **GIVEN** {ac.get('given', '')}")
                lines.append(f"- **WHEN** {ac.get('when', '')}")
                lines.append(f"- **THEN** {ac.get('then', '')}")
                lines.append("")
            else:
                lines.append(f"- {ac}")
        lines.append("")

    # Related edge cases from spec
    edge_cases = spec.get("edge_cases", [])
    if edge_cases:
        lines.append("## Edge Cases to Consider")
        for e in edge_cases:
            lines.append(f"- {e}")
        lines.append("")

    # NFRs
    nfrs = spec.get("non_functional_requirements", [])
    if nfrs:
        lines.append("## Non-Functional Requirements")
        for n in nfrs:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Synapse AI SDLC Platform*")
    return "\n".join(lines)


def _build_subtask_description(subtask, plan):
    """Rich subtask description for developers/AI agents — actionable with file paths and context."""
    lines = []
    desc = subtask.get("description", "")
    hours = subtask.get("estimated_hours", 0)
    story_id = subtask.get("story_id", "")

    lines.append("## Task Description")
    lines.append(desc if desc else "No description provided.")
    lines.append("")

    if story_id:
        lines.append(f"**Linked Story:** `{story_id}`")
    if hours:
        lines.append(f"**Estimated:** {hours}h")
    lines.append("")

    # Find affected routes relevant to this subtask
    routes = plan.get("affected_routes", [])
    if routes:
        lines.append("## Affected Routes / Files")
        for r in routes:
            if isinstance(r, dict):
                lines.append(
                    f"- `{r.get('method', '')} {r.get('path', '')}` in `{r.get('file', '')}` — {r.get('change', '')}")
            else:
                lines.append(f"- {r}")
        lines.append("")

    # Data flow steps
    data_flow = plan.get("data_flow", [])
    if data_flow:
        lines.append("## Data Flow")
        for step in data_flow:
            if isinstance(step, dict):
                lines.append(
                    f"- **{step.get('component', '')}**: {step.get('description', '')}")
            else:
                lines.append(f"- {step}")
        lines.append("")

    # Migrations
    migrations = plan.get("migrations", [])
    if migrations:
        lines.append("## Database Migrations")
        for m in migrations:
            if isinstance(m, dict):
                lines.append(
                    f"- `{m.get('table', '')}`: {m.get('change', '')}")
                if m.get("sql_hint"):
                    lines.append(f"```\n{m['sql_hint']}\n```")
            else:
                lines.append(f"- {m}")
        lines.append("")

    # Risks
    risks = plan.get("risks", [])
    if risks:
        lines.append("## Risks")
        for r in risks:
            if isinstance(r, dict):
                lines.append(
                    f"- **[{r.get('severity', 'medium').upper()}]** {r.get('description', '')} — *Mitigation:* {r.get('mitigation', '')}")
        lines.append("")

    lines.append("---")
    lines.append(
        "*Generated by Synapse AI SDLC Platform — this ticket contains enough context for an AI coding agent to implement.*")
    return "\n".join(lines)


def _build_test_description(tc, suite):
    """Rich test case description for QA — step-by-step with expected results."""
    lines = []

    lines.append(f"## {tc.get('title', 'Test Case')}")
    if tc.get("description"):
        lines.append(tc["description"])
    lines.append("")

    lines.append(
        f"**Suite:** {suite.get('name', '')} ({suite.get('type', '')})")
    lines.append(f"**Priority:** {tc.get('priority', 'medium')}")
    lines.append(f"**Automated:** {'Yes' if tc.get('automated') else 'No'}")
    lines.append("")

    # Preconditions
    preconditions = tc.get("preconditions", [])
    if preconditions:
        lines.append("## Preconditions")
        for p in preconditions:
            lines.append(f"- {p}")
        lines.append("")

    # Steps
    steps = tc.get("steps", [])
    if steps:
        lines.append("## Steps")
        for i, step in enumerate(steps, 1):
            lines.append(f"- **Step {i}:** {step}")
        lines.append("")

    # Expected result
    if tc.get("expected_result"):
        lines.append("## Expected Result")
        lines.append(tc["expected_result"])
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Synapse AI SDLC Platform*")
    return "\n".join(lines)


@celery_app.task(bind=True, name="app.workers.tasks.jira_export_task", time_limit=600, max_retries=0)
def jira_export_task(self, feature_id: str, project_key_override: str = None):
    """Create Jira tickets (Epic + Stories + Sub-tasks + Tests) from feature artifacts."""
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        from app.models.jira_config import JiraConfig
        from app.models.jira_issue_link import JiraIssueLink
        from app.models.artifact import Artifact
        from app.utils.crypto import decrypt_token
        from app.services.jira_service import JiraService

        feature = session.get(Feature, feature_id)
        if not feature:
            return

        config = session.execute(
            select(JiraConfig).where(
                JiraConfig.project_id == feature.project_id)
        ).scalars().first()
        if not config:
            logger.error(f"No Jira config for project {feature.project_id}")
            return

        # Incremental export: check which types already exist
        existing_links = session.execute(
            select(JiraIssueLink).where(JiraIssueLink.feature_id == feature_id)
        ).scalars().all()
        existing_types = {l.issue_type for l in existing_links}

        token = decrypt_token(config.api_token_encrypted)
        svc = JiraService(config.site_url, config.user_email, token)
        project_key = project_key_override or config.default_project_key

        _publish_feature_event(
            feature_id, {"type": "jira_export", "step": "Starting Jira export..."})

        # Load artifacts
        spec_content = {}
        plan_content = {}
        tests_content = {}
        if feature.spec_artifact_id:
            art = session.get(Artifact, feature.spec_artifact_id)
            if art:
                spec_content = art.content if isinstance(
                    art.content, dict) else {}
        if feature.plan_artifact_id:
            art = session.get(Artifact, feature.plan_artifact_id)
            if art:
                plan_content = art.content if isinstance(
                    art.content, dict) else {}
        if feature.tests_artifact_id:
            art = session.get(Artifact, feature.tests_artifact_id)
            if art:
                tests_content = art.content if isinstance(
                    art.content, dict) else {}

        # Build story_key_map from existing links (for incremental exports)
        story_key_map = {}
        for l in existing_links:
            if l.issue_type == "story" and l.source_item_id:
                story_key_map[l.source_item_id] = l.issue_key

        # Reuse existing epic key or get from feature
        epic_key = feature.jira_epic_key
        for l in existing_links:
            if l.issue_type == "epic":
                epic_key = l.issue_key
                break

        created = 0

        # --- Epic + Stories (from spec) ---
        if "epic" not in existing_types and spec_content:
            feature_name = spec_content.get(
                "feature_name", feature.description)
            epic_desc = _build_epic_description(
                spec_content, plan_content, tests_content)
            epic = asyncio.run(svc.create_epic(
                project_key, feature_name, epic_desc))
            epic_key = epic["key"]
            feature.jira_epic_key = epic_key
            session.commit()

            session.add(JiraIssueLink(
                feature_id=feature_id, issue_key=epic_key, issue_type="epic",
                issue_url=epic["url"], summary=feature_name,
                source_artifact_id=feature.spec_artifact_id,
            ))
            created += 1
            _publish_feature_event(
                feature_id, {"type": "jira_export", "step": f"Epic created: {epic_key}"})

            for story in spec_content.get("user_stories", []):
                story_id = story.get("id", "")
                summary = f"[{story_id}] As a {story.get('role', 'user')}, I want {story.get('action', '...')}"
                desc = _build_story_description(story, spec_content)

                result = asyncio.run(svc.create_story(
                    project_key, summary[:250], desc, epic_key=epic_key))
                story_key_map[story_id] = result["key"]
                session.add(JiraIssueLink(
                    feature_id=feature_id, issue_key=result["key"], issue_type="story",
                    issue_url=result["url"], summary=summary[:250],
                    parent_issue_key=epic_key,
                    source_artifact_id=feature.spec_artifact_id, source_item_id=story_id,
                ))
                created += 1
                _publish_feature_event(
                    feature_id, {"type": "jira_export", "step": f"Story created: {result['key']}"})

        # --- Subtasks (from plan) ---
        if "subtask" not in existing_types and plan_content and epic_key:
            for subtask in plan_content.get("subtasks", []):
                parent_key = story_key_map.get(
                    subtask.get("story_id"), epic_key)
                summary = f"[{subtask.get('id', '')}] {subtask.get('title', 'Subtask')}"
                desc = _build_subtask_description(subtask, plan_content)

                result = asyncio.run(svc.create_subtask(
                    project_key, summary[:250], desc, parent_key=parent_key))
                session.add(JiraIssueLink(
                    feature_id=feature_id, issue_key=result["key"], issue_type="subtask",
                    issue_url=result["url"], summary=summary[:250],
                    parent_issue_key=parent_key,
                    source_artifact_id=feature.plan_artifact_id, source_item_id=subtask.get(
                        "id", ""),
                ))
                created += 1
                _publish_feature_event(
                    feature_id, {"type": "jira_export", "step": f"Subtask created: {result['key']}"})

        # --- Test cases (from tests) ---
        if "test" not in existing_types and tests_content and epic_key:
            for suite in tests_content.get("test_suites", []):
                parent_key = story_key_map.get(suite.get("story_id"), epic_key)
                for tc in suite.get("test_cases", []):
                    summary = f"[QA] [{tc.get('id', '')}] {tc.get('title', 'Test case')}"
                    desc = _build_test_description(tc, suite)
                    result = asyncio.run(svc.create_subtask(
                        project_key, summary[:250], desc,
                        parent_key=parent_key,
                    ))
                    session.add(JiraIssueLink(
                        feature_id=feature_id, issue_key=result["key"], issue_type="test",
                        issue_url=result["url"], summary=summary[:250],
                        parent_issue_key=parent_key,
                        source_artifact_id=feature.tests_artifact_id, source_item_id=tc.get(
                            "id", ""),
                    ))
                    created += 1

        session.commit()

        if created == 0:
            _publish_feature_event(feature_id, {
                "type": "jira_export", "step": "Nothing new to export", "done": True,
            })
        else:
            _publish_feature_event(feature_id, {
                "type": "jira_export",
                "step": f"Export complete: {created} new issues created",
                "epic_key": epic_key,
                "done": True,
            })

    except Exception as e:
        logger.exception(f"Jira export failed for feature {feature_id}")
        _publish_feature_event(
            feature_id, {"type": "error", "message": f"Jira export failed: {e}"})
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.jira_sync_task", time_limit=120, max_retries=0)
def jira_sync_task(self, feature_id: str):
    """Batch-refresh Jira issue statuses for a feature."""
    from datetime import datetime
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        from app.models.jira_config import JiraConfig
        from app.models.jira_issue_link import JiraIssueLink
        from app.utils.crypto import decrypt_token
        from app.services.jira_service import JiraService

        feature = session.get(Feature, feature_id)
        if not feature:
            return

        config = session.execute(
            select(JiraConfig).where(
                JiraConfig.project_id == feature.project_id)
        ).scalars().first()
        if not config:
            return

        token = decrypt_token(config.api_token_encrypted)
        svc = JiraService(config.site_url, config.user_email, token)

        links = session.execute(
            select(JiraIssueLink).where(JiraIssueLink.feature_id == feature_id)
        ).scalars().all()

        if not links:
            return

        issue_keys = [l.issue_key for l in links]
        issues = asyncio.run(svc.bulk_get_issues(issue_keys))
        status_map = {
            i["key"]: i["fields"]["status"]["name"]
            for i in issues
        }

        now = datetime.utcnow()
        for link in links:
            if link.issue_key in status_map:
                link.status = status_map[link.issue_key]
                link.status_synced_at = now

        session.commit()
        _publish_feature_event(
            feature_id, {"type": "jira_sync", "synced": len(links)})

    except Exception as e:
        logger.exception(f"Jira sync failed for feature {feature_id}")
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.pr_sync_task", time_limit=120, max_retries=0)
def pr_sync_task(self, feature_id: str):
    """Sync all open PRs for a feature, detect merges."""
    from datetime import datetime
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        from app.models.pr_link import PullRequestLink
        from app.services.github_service import GitHubService

        feature = session.get(Feature, feature_id)
        if not feature:
            return

        # Get GitHub token
        from app.models.repository import Repository
        from app.models.project import Project
        from app.utils.crypto import decrypt_token

        token = None
        repos = session.execute(
            select(Repository).where(
                Repository.project_id == feature.project_id)
        ).scalars().all()
        for r in repos:
            if r.github_token_encrypted:
                token = decrypt_token(r.github_token_encrypted)
                break
        if not token:
            project = session.get(Project, feature.project_id)
            if project and project.github_token_encrypted:
                token = decrypt_token(project.github_token_encrypted)
        if not token:
            logger.warning(f"No GitHub token for feature {feature_id}")
            return

        svc = GitHubService(token)
        links = session.execute(
            select(PullRequestLink).where(
                PullRequestLink.feature_id == feature_id,
                PullRequestLink.state == "open",
            )
        ).scalars().all()

        for link in links:
            try:
                owner, repo = link.repo_full_name.split("/", 1)
                pr_data = asyncio.run(svc.get_pull_request(
                    owner, repo, link.pr_number))
                link.state = "merged" if pr_data.get(
                    "merged_at") else pr_data.get("state", "open")
                link.merged_at = pr_data.get("merged_at")
                link.synced_at = datetime.utcnow()

                if link.state == "merged" and not link.kb_updated:
                    link.files_changed = asyncio.run(
                        svc.get_pr_files(owner, repo, link.pr_number))
                    link.commit_messages = asyncio.run(
                        svc.get_pr_commits(owner, repo, link.pr_number))
                    diff = asyncio.run(svc.get_pr_diff(
                        owner, repo, link.pr_number))
                    link.diff_summary = diff[:5000] if diff else None
                    pr_kb_update_task.delay(str(feature_id), str(link.id))
            except Exception as e:
                logger.warning(f"Failed to sync PR {link.pr_url}: {e}")

        session.commit()

    except Exception as e:
        logger.exception(f"PR sync failed for feature {feature_id}")
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.pr_kb_update_task", time_limit=600, max_retries=0)
def pr_kb_update_task(self, feature_id: str, pr_link_id: str):
    """Update KB with implementation delta from a merged PR."""
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        from app.models.pr_link import PullRequestLink

        feature = session.get(Feature, feature_id)
        pr_link = session.get(PullRequestLink, pr_link_id)
        if not feature or not pr_link:
            return

        _publish_feature_event(
            feature_id, {"type": "thinking", "message": "Updating KB from merged PR..."})

        # Build prompt with PR data
        pr_context = (
            f"## Merged PR: {pr_link.title}\n"
            f"URL: {pr_link.pr_url}\n"
            f"Merged at: {pr_link.merged_at}\n\n"
        )
        if pr_link.files_changed:
            pr_context += "### Files Changed:\n"
            for f in (pr_link.files_changed if isinstance(pr_link.files_changed, list) else []):
                pr_context += f"- {f.get('filename', '?')} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})\n"
        if pr_link.commit_messages:
            pr_context += "\n### Commit Messages:\n"
            for c in (pr_link.commit_messages if isinstance(pr_link.commit_messages, list) else []):
                pr_context += f"- {c.get('message', '?')}\n"
        if pr_link.diff_summary:
            pr_context += f"\n### Diff Summary (first 3000 chars):\n{pr_link.diff_summary[:3000]}\n"

        artifact_refs = []
        if feature.spec_artifact_id:
            artifact_refs.append(f"spec: {feature.spec_artifact_id}")
        if feature.plan_artifact_id:
            artifact_refs.append(f"plan: {feature.plan_artifact_id}")
        if feature.tests_artifact_id:
            artifact_refs.append(f"tests: {feature.tests_artifact_id}")

        message = (
            f"Feature: {feature.description}\n\n"
            f"Artifact IDs:\n" +
            "\n".join(f"- {a}" for a in artifact_refs) + "\n\n"
            f"{pr_context}\n\n"
            f"Read the existing artifacts and the PR data above. "
            f"Generate an updated KB entry that compares what was PLANNED vs ACTUALLY IMPLEMENTED. "
            f"Follow the kb-update-from-pr skill instructions."
        )

        provider = get_provider()
        from core.orchestrator.loop import agent_loop
        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=message,
            skill_name="kb-update-from-pr",
        ))

        if result.get("artifact_id"):
            from app.models.artifact import Artifact
            from pathlib import Path

            artifact_path = Path("./artifacts") / str(feature.project_id) / f"{result['artifact_id']}.json"
            if not artifact_path.exists():
                artifact_path = Path("./artifacts") / f"{result['artifact_id']}.json"
            if artifact_path.exists():
                artifact_data = json.loads(artifact_path.read_text())
                content = artifact_data.get("content", "")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        content = {"raw": content}

                db_artifact = Artifact(
                    id=artifact_data["id"],
                    type="kb",
                    name=artifact_data.get(
                        "name", f"KB (PR update): {feature.description}"),
                    content=content,
                    parent_id=feature.tests_artifact_id or feature.plan_artifact_id,
                    status="approved",
                    version=1,
                    project_id=feature.project_id,
                )
                session.merge(db_artifact)

        pr_link.kb_updated = True
        session.commit()
        _publish_feature_event(
            feature_id, {"type": "kb_updated", "pr_url": pr_link.pr_url})

    except Exception as e:
        logger.exception(f"PR KB update failed for feature {feature_id}")
    finally:
        session.close()


def parse_jira_key_from_branch(branch: str) -> Optional[str]:
    """Extract a Jira issue key from a branch name.

    Matches patterns like feature/SYN-6, feature/SYN_6, feature/SYN-6-some-slug,
    feat/ABC-123, etc.  Returns the uppercased, dash-normalised key or None.
    """
    m = re.search(r'(?:^|/)([A-Za-z]{2,10}[-_]\d+)', branch)
    return m.group(1).upper().replace('_', '-') if m else None


def _auto_create_pr_link(
    session: Session,
    repo_full_name: str,
    pr_number: int,
    pr_payload: dict,
) -> None:
    """Auto-create a PullRequestLink when a PR is opened with a Jira-keyed branch.

    Searches for the Jira issue key in:
      1. Branch name (head_branch field)
      2. PR title (fallback when branch has no key)

    Then finds the owning feature via jira_issue_links or feature.jira_epic_key.
    """
    from app.models.pr_link import PullRequestLink
    from app.models.jira_issue_link import JiraIssueLink
    from app.models.feature import Feature

    branch_name = pr_payload.get("head_branch", "")
    pr_title = pr_payload.get("title", "")

    # Try branch name first, then PR title as fallback
    _title_match = re.search(r'\b([A-Za-z]{2,10}[-_]\d+)\b', pr_title)
    jira_key = (
        parse_jira_key_from_branch(branch_name)
        or (_title_match and _title_match.group(1).upper().replace('_', '-'))
    )

    if not jira_key:
        logger.debug(
            "webhook_pr_update_task: no Jira key in branch %r or title %r, skipping auto-create",
            branch_name, pr_title)
        return

    # Primary lookup: jira_issue_links table
    issue_link = session.execute(
        select(JiraIssueLink).where(JiraIssueLink.issue_key == jira_key)
    ).scalar_one_or_none()

    if issue_link:
        target_feature_id = issue_link.feature_id
    else:
        # Fallback: match against feature.jira_epic_key
        feature_row = session.execute(
            select(Feature).where(Feature.jira_epic_key == jira_key)
        ).scalar_one_or_none()
        if feature_row:
            target_feature_id = feature_row.id
        else:
            logger.warning(
                "webhook_pr_update_task: no feature found for Jira key %s "
                "(branch=%r, pr=%s#%s) — Jira export may not have run yet",
                jira_key, branch_name, repo_full_name, pr_number)
            return

    pr_url = f"https://github.com/{repo_full_name}/pull/{pr_number}"

    # Determine state from the payload (handles both opened and closed events)
    merged = pr_payload.get("merged", False)
    if merged:
        state = "merged"
    elif pr_payload.get("state") == "closed":
        state = "closed"
    else:
        state = "open"

    link = PullRequestLink(
        feature_id=target_feature_id,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_url=pr_url,
        title=pr_title or f"PR #{pr_number}",
        state=state,
        merged_at=pr_payload.get("merged_at"),
        branch_name=branch_name or None,
        jira_issue_key=jira_key,
    )
    session.add(link)
    session.flush()  # get link.id

    # Fetch commits, files, and diff from GitHub
    try:
        from app.models.repository import Repository
        from app.models.project import Project
        from app.utils.crypto import decrypt_token
        from app.services.github_service import GitHubService

        feature = session.get(Feature, target_feature_id)
        token = None
        if feature:
            repos = session.execute(
                select(Repository).where(
                    Repository.project_id == feature.project_id)
            ).scalars().all()
            for r in repos:
                if r.github_token_encrypted:
                    token = decrypt_token(r.github_token_encrypted)
                    break
            if not token:
                project = session.get(Project, feature.project_id)
                if project and project.github_token_encrypted:
                    token = decrypt_token(project.github_token_encrypted)

        if token:
            svc = GitHubService(token)
            owner, repo = repo_full_name.split("/", 1)
            link.commit_messages = asyncio.run(
                svc.get_pr_commits(owner, repo, pr_number))
            link.files_changed = asyncio.run(
                svc.get_pr_files(owner, repo, pr_number))
            diff = asyncio.run(
                svc.get_pr_diff(owner, repo, pr_number))
            link.diff_summary = diff[:5000] if diff else None
    except Exception as e:
        logger.warning(
            "Failed to fetch PR details during auto-create for %s#%s: %s",
            repo_full_name, pr_number, e)

    session.commit()

    event_type = "pr_merged" if state == "merged" else "pr_opened"
    _publish_feature_event(str(target_feature_id), {
        "type": event_type,
        "pr_url": pr_url,
        "branch": branch_name,
        "jira_issue_key": jira_key,
    })
    logger.info(
        "Auto-created PR link for %s#%s → feature %s (jira_key=%s, state=%s)",
        repo_full_name, pr_number, target_feature_id, jira_key, state)


@celery_app.task(bind=True, name="app.workers.tasks.webhook_pr_update_task", time_limit=120, max_retries=0)
def webhook_pr_update_task(
    self,
    repo_full_name: str,
    pr_number: int,
    action: str,
    pr_payload: dict,
):
    """Process a pull_request webhook event (opened / synchronize / closed).

    Matches the incoming repo+PR number to PullRequestLink rows and updates
    state, triggering KB generation on merge just like pr_sync_task does.
    """
    from datetime import datetime

    logger.info(
        "webhook_pr_update_task: action=%s repo=%s pr=%s branch=%r title=%r",
        action, repo_full_name, pr_number,
        pr_payload.get("head_branch", ""), pr_payload.get("title", ""))

    session = _get_sync_session()
    try:
        from app.models.pr_link import PullRequestLink
        from app.models.feature import Feature
        from app.services.github_service import GitHubService

        links = session.execute(
            select(PullRequestLink).where(
                PullRequestLink.repo_full_name == repo_full_name,
                PullRequestLink.pr_number == pr_number,
            )
        ).scalars().all()

        if not links:
            if action in ("opened", "closed"):
                _auto_create_pr_link(
                    session, repo_full_name, pr_number, pr_payload)
            else:
                logger.debug(
                    "webhook_pr_update_task: no links found for %s#%s",
                    repo_full_name, pr_number)
            return

        for link in links:
            feature_id = str(link.feature_id)

            # Always update title and branch from the latest payload
            new_title = pr_payload.get("title", "")
            if new_title and new_title != link.title:
                link.title = new_title
            branch_name = pr_payload.get("head_branch", "")
            if branch_name and not link.branch_name:
                link.branch_name = branch_name

            # Re-extract jira key on open/edit events only (not on close)
            if action in ("opened", "edited", "synchronize"):
                _title_match = re.search(
                    r'\b([A-Za-z]{2,10}[-_]\d+)\b', new_title or link.title or "")
                new_jira_key = (
                    parse_jira_key_from_branch(
                        branch_name or link.branch_name or "")
                    or (_title_match and _title_match.group(1).upper().replace('_', '-'))
                )
                if new_jira_key and new_jira_key != link.jira_issue_key:
                    link.jira_issue_key = new_jira_key

            if action == "edited":
                # Title/body edit only — just persist the title update above
                link.synced_at = datetime.utcnow()
                _publish_feature_event(feature_id, {
                    "type": "pr_updated",
                    "pr_url": link.pr_url,
                    "action": "edited",
                })

            elif action == "synchronize":
                # New commits pushed — fetch fresh diff/files/commits via GitHub API
                feature = session.get(Feature, link.feature_id)
                if feature:
                    from app.models.repository import Repository
                    from app.models.project import Project
                    from app.utils.crypto import decrypt_token

                    token = None
                    repos = session.execute(
                        select(Repository).where(
                            Repository.project_id == feature.project_id)
                    ).scalars().all()
                    for r in repos:
                        if r.github_token_encrypted:
                            token = decrypt_token(r.github_token_encrypted)
                            break
                    if not token:
                        project = session.get(Project, feature.project_id)
                        if project and project.github_token_encrypted:
                            token = decrypt_token(
                                project.github_token_encrypted)

                    if token:
                        svc = GitHubService(token)
                        owner, repo = repo_full_name.split("/", 1)
                        try:
                            link.files_changed = asyncio.run(
                                svc.get_pr_files(owner, repo, pr_number))
                            link.commit_messages = asyncio.run(
                                svc.get_pr_commits(owner, repo, pr_number))
                            diff = asyncio.run(
                                svc.get_pr_diff(owner, repo, pr_number))
                            link.diff_summary = diff[:5000] if diff else None
                        except Exception as e:
                            logger.warning(
                                "Failed to fetch PR details for %s#%s: %s", repo_full_name, pr_number, e)

                link.synced_at = datetime.utcnow()
                _publish_feature_event(feature_id, {
                    "type": "pr_updated",
                    "pr_url": link.pr_url,
                    "action": "synchronize",
                    "head_sha": pr_payload.get("head_sha", ""),
                })

            elif action == "closed":
                merged = pr_payload.get("merged", False)
                if merged:
                    link.state = "merged"
                    link.merged_at = pr_payload.get("merged_at")

                    # Fetch diff/files if not already present
                    if not link.diff_summary:
                        feature = session.get(Feature, link.feature_id)
                        if feature:
                            from app.models.repository import Repository
                            from app.models.project import Project
                            from app.utils.crypto import decrypt_token

                            token = None
                            repos = session.execute(
                                select(Repository).where(
                                    Repository.project_id == feature.project_id)
                            ).scalars().all()
                            for r in repos:
                                if r.github_token_encrypted:
                                    token = decrypt_token(
                                        r.github_token_encrypted)
                                    break
                            if not token:
                                project = session.get(
                                    Project, feature.project_id)
                                if project and project.github_token_encrypted:
                                    token = decrypt_token(
                                        project.github_token_encrypted)

                            if token:
                                svc = GitHubService(token)
                                owner, repo = repo_full_name.split("/", 1)
                                try:
                                    link.files_changed = asyncio.run(
                                        svc.get_pr_files(owner, repo, pr_number))
                                    link.commit_messages = asyncio.run(
                                        svc.get_pr_commits(owner, repo, pr_number))
                                    diff = asyncio.run(
                                        svc.get_pr_diff(owner, repo, pr_number))
                                    link.diff_summary = diff[:5000] if diff else None
                                except Exception as e:
                                    logger.warning(
                                        "Failed to fetch PR details for %s#%s: %s", repo_full_name, pr_number, e)

                    if not link.kb_updated:
                        pr_kb_update_task.delay(feature_id, str(link.id))

                    _publish_feature_event(feature_id, {
                        "type": "pr_merged",
                        "pr_url": link.pr_url,
                        "merged_at": link.merged_at,
                    })
                else:
                    link.state = "closed"
                    _publish_feature_event(feature_id, {
                        "type": "pr_closed",
                        "pr_url": link.pr_url,
                    })

                link.synced_at = datetime.utcnow()

            elif action == "opened":
                # PR opened/reopened — refresh state and fetch latest commits/files
                link.state = "open"
                link.synced_at = datetime.utcnow()

                feature = session.get(Feature, link.feature_id)
                if feature:
                    from app.models.repository import Repository
                    from app.models.project import Project
                    from app.utils.crypto import decrypt_token

                    token = None
                    repos = session.execute(
                        select(Repository).where(
                            Repository.project_id == feature.project_id)
                    ).scalars().all()
                    for r in repos:
                        if r.github_token_encrypted:
                            token = decrypt_token(r.github_token_encrypted)
                            break
                    if not token:
                        project = session.get(Project, feature.project_id)
                        if project and project.github_token_encrypted:
                            token = decrypt_token(
                                project.github_token_encrypted)

                    if token:
                        svc = GitHubService(token)
                        owner, repo = repo_full_name.split("/", 1)
                        try:
                            link.commit_messages = asyncio.run(
                                svc.get_pr_commits(owner, repo, pr_number))
                            link.files_changed = asyncio.run(
                                svc.get_pr_files(owner, repo, pr_number))
                            diff = asyncio.run(
                                svc.get_pr_diff(owner, repo, pr_number))
                            link.diff_summary = diff[:5000] if diff else None
                        except Exception as e:
                            logger.warning(
                                "Failed to fetch PR details on open for %s#%s: %s",
                                repo_full_name, pr_number, e)

                _publish_feature_event(feature_id, {
                    "type": "pr_opened",
                    "pr_url": link.pr_url,
                })

        session.commit()

    except Exception as e:
        logger.exception(
            "webhook_pr_update_task failed for %s#%s", repo_full_name, pr_number)
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.webhook_deployment_task", time_limit=60, max_retries=0)
def webhook_deployment_task(self, repo_full_name: str, head_branch: str, run_payload: dict):
    """Process a workflow_run completed=success webhook event.

    Matches by repo_full_name and head_branch against open/merged PullRequestLink rows
    and stores the deployment status. Publishes a deployment_success SSE event.
    """
    from datetime import datetime

    session = _get_sync_session()
    try:
        from app.models.pr_link import PullRequestLink

        # Start with links for the repository, then narrow to the workflow run's PRs.
        query = select(PullRequestLink).where(
            PullRequestLink.repo_full_name == repo_full_name,
        )
        links = session.execute(query).scalars().all()

        # Use pull_requests from the workflow_run payload to narrow precisely
        pull_requests = run_payload.get("pull_requests") or []
        pr_urls = set()
        pr_numbers = set()
        for pr in pull_requests:
            if not isinstance(pr, dict):
                continue
            html_url = pr.get("html_url")
            if html_url:
                pr_urls.add(html_url)
            number = pr.get("number")
            if number is not None:
                pr_numbers.add(str(number))

        if pr_urls or pr_numbers:
            # Narrow to open/merged links that correspond to the workflow run's PRs
            matched = []
            for lnk in links:
                if lnk.state not in ("open", "merged"):
                    continue
                if lnk.pr_url in pr_urls:
                    matched.append(lnk)
                    continue
                if any(lnk.pr_url.endswith(f"/pull/{n}") for n in pr_numbers):
                    matched.append(lnk)
        else:
            # Fallback: no PR references in payload — match all open/merged for this repo
            logger.debug(
                "webhook_deployment_task: no PR references in payload for %s branch=%s, "
                "falling back to all open/merged links",
                repo_full_name, head_branch,
            )
            matched = [
                lnk for lnk in links
                if lnk.state in ("open", "merged")
            ]

        if not matched:
            logger.debug(
                "webhook_deployment_task: no matching links for %s branch=%s",
                repo_full_name, head_branch,
            )
            return

        deployment_record = {
            "branch": head_branch,
            "run_id": run_payload.get("run_id"),
            "run_url": run_payload.get("run_url", ""),
            "name": run_payload.get("name", ""),
            "conclusion": run_payload.get("conclusion", "success"),
            "completed_at": run_payload.get("completed_at"),
            "head_sha": run_payload.get("head_sha", ""),
        }

        for link in matched:
            feature_id = str(link.feature_id)
            link.deployment_status = deployment_record
            _publish_feature_event(feature_id, {
                "type": "deployment_success",
                "pr_url": link.pr_url,
                "branch": head_branch,
                "run_url": run_payload.get("run_url", ""),
                "workflow_name": run_payload.get("name", ""),
            })

        session.commit()

    except Exception as e:
        logger.exception(
            "webhook_deployment_task failed for %s branch=%s", repo_full_name, head_branch)
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.knowledge_query_task", time_limit=120, max_retries=0)
def knowledge_query_task(self, project_id: str, question: str, persona: str, query_id: str):
    """Answer a knowledge query using the project's accumulated knowledge."""
    session = _get_sync_session()
    try:
        from app.models.knowledge_entry import KnowledgeEntry

        # Pre-fetch relevant knowledge
        entries = session.execute(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.project_id == project_id)
            .order_by(KnowledgeEntry.created_at.desc())
            .limit(20)
        ).scalars().all()

        knowledge_context = ""
        if entries:
            knowledge_context = "\n\n## Project Knowledge Base\n"
            for e in entries:
                knowledge_context += f"\n### [{e.entry_type}] {e.title}\n{e.content[:300]}\n"

        # Build codebase context
        from app.models.repository import Repository
        repos = session.execute(
            select(Repository).where(
                Repository.project_id == project_id,
                Repository.analysis_status == "ready",
            )
        ).scalars().all()

        codebase_context = ""
        for r in repos:
            if r.codebase_context:
                codebase_context += f"\n\n## Repository: {r.name}\n{r.codebase_context}"

        codebase_context += knowledge_context

        _publish_event(f"knowledge:{query_id}", {
                       "type": "thinking", "message": "Searching knowledge base..."})

        # --- Tier 1: Vector search for targeted context ---
        vector_context = ""
        try:
            from core.indexer.vector_store import VectorStore
            store = VectorStore()

            # Search knowledge base (decisions, patterns, lessons)
            kb_results = store.search_knowledge(
                project_id, question, n_results=5)
            if kb_results:
                vector_context += "\n\n## Relevant Knowledge (vector search)\n"
                for r in kb_results:
                    vector_context += f"\n- {r['content'][:500]}\n"

            # Search codebase for implementation details
            repo_ids = [str(r.id) for r in repos]
            if repo_ids:
                code_results = store.search_all_repos(
                    project_id, repo_ids, question, n_results=5)
                if code_results:
                    vector_context += "\n\n## Relevant Code (vector search)\n"
                    for r in code_results:
                        meta = r.get("metadata", {})
                        vector_context += f"\n### {meta.get('file', 'unknown')} (lines {meta.get('start_line', '?')}-{meta.get('end_line', '?')})\n```\n{r['content'][:600]}\n```\n"
        except Exception as e:
            logger.warning(f"Vector search failed for knowledge query: {e}")

        # --- Tier 2: Direct LLM call (no agent loop, no tools) ---
        persona_guides = {
            "po": "Focus on business context, user impact, feature scope. Avoid code details. Use business language.",
            "qa": "Focus on test coverage, affected areas, regression risk, edge cases. Include which repos/components are impacted.",
            "developer": "Focus on code patterns, API contracts, implementation details. Include file paths and function signatures.",
            "tech_lead": "Focus on architecture decisions, trade-offs, risks, and cross-repo dependencies. Include file paths.",
        }
        persona_guide = persona_guides.get(
            persona, persona_guides["developer"])

        system_prompt = f"""You are a Synapse knowledge assistant. Answer the user's question
based on the provided project context. Be specific and cite sources.

## Persona: {persona}
{persona_guide}

## Response Format
1. **Direct answer** to the question
2. **Details** with supporting evidence (persona-appropriate depth)
3. **Sources** cited: [file:path:line] or [KB: entry_title] or [Architecture: section]
4. **Related questions** the user might want to ask next

## Rules
- Always cite sources — never answer from general knowledge alone
- If you can't find the answer in the context, say so clearly
- Keep answers concise: PO answers under 200 words, Dev/TL answers can be longer with code

{codebase_context}

{vector_context}
"""

        provider = get_provider()
        result = asyncio.run(provider.chat(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": question}],
            tools=[],
            max_tokens=4096,
        ))

        answer = result.get("content", "No answer found.")

        _publish_event(f"knowledge:{query_id}", {
            "type": "response",
            "content": answer,
            "done": True,
        })

    except Exception as e:
        logger.exception(f"Knowledge query failed: {question}")
        _publish_event(f"knowledge:{query_id}", {
                       "type": "error", "message": str(e)})
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.synthesize_project_task", time_limit=600, max_retries=0)
def synthesize_project_task(self, project_id: str):
    """Generate unified project architecture from all per-repo architectures.

    Triggered when all repos in a project are analyzed and ready.
    Reads per-repo architecture artifacts, identifies cross-repo API contracts,
    shared models, and request flows, then stores a project_architecture artifact.
    """
    session = _get_sync_session()
    try:
        from app.models.project import Project
        from app.models.repository import Repository
        from app.models.artifact import Artifact

        project = session.get(Project, project_id)
        if not project:
            return

        repos = session.execute(
            select(Repository).where(Repository.project_id == project_id)
        ).scalars().all()

        if len(repos) < 2:
            logger.info(
                f"Project {project_id} has <2 repos, skipping synthesis")
            return

        # Collect per-repo architecture artifact IDs
        arch_artifacts = session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "architecture",
            )
        ).scalars().all()

        if not arch_artifacts:
            logger.warning(
                f"No architecture artifacts for project {project_id}")
            return

        _publish_project_event(
            project_id, {"type": "step", "step": "Synthesizing cross-repo architecture..."})

        # Build message with all architecture artifact IDs
        arch_refs = "\n".join(
            f"- {a.name or 'Architecture'} (ID: {a.id})"
            for a in arch_artifacts
        )
        repo_summary = "\n".join(
            f"- {r.name} ({r.repo_type or 'unknown'}): {r.github_url}"
            for r in repos
        )

        message = (
            f"This project has {len(repos)} repositories:\n{repo_summary}\n\n"
            f"Architecture artifacts:\n{arch_refs}\n\n"
            f"Read each architecture artifact using get_artifact. "
            f"Then generate a unified project architecture that shows how these repos connect. "
            f"Follow the project-synthesis skill instructions. "
            f"Store the result using store_artifact with type='project_architecture'."
        )

        # Combine codebase contexts
        codebase_context = "\n\n".join(
            f"## Repository: {r.name} ({r.repo_type or 'unknown'})\n{r.codebase_context}"
            for r in repos if r.codebase_context
        )

        provider = get_provider()
        from core.orchestrator.loop import agent_loop
        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=message,
            skill_name="project-synthesis",
            codebase_context=codebase_context,
        ))
        logger.info(
            f"Project synthesis completed for {project_id} in {result['turns']} turns")

        # Save project_architecture artifact to DB
        if result.get("artifact_id"):
            from pathlib import Path
            artifact_path = Path("./artifacts") / str(feature.project_id) / f"{result['artifact_id']}.json"
            if not artifact_path.exists():
                artifact_path = Path("./artifacts") / f"{result['artifact_id']}.json"
            if artifact_path.exists():
                artifact_data = json.loads(artifact_path.read_text())
                content = artifact_data.get("content", "")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        content = {"raw": content}

                db_artifact = Artifact(
                    id=artifact_data["id"],
                    type="project_architecture",
                    name=artifact_data.get(
                        "name", f"Unified Architecture: {project.name}"),
                    content=content,
                    status="approved",
                    version=1,
                    project_id=project_id,
                )
                session.merge(db_artifact)
                session.commit()

        _publish_project_event(
            project_id, {"type": "status", "status": "synthesis_complete"})

    except Exception as e:
        logger.exception(f"Project synthesis failed for {project_id}")
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.kb_accumulate_task", time_limit=300, max_retries=0)
def kb_accumulate_task(self, feature_id: str, kb_artifact_id: str):
    """Extract knowledge entries from a KB artifact and update accumulated KB.

    Triggered after kb_update_task creates a feature-level KB artifact.
    Parses the KB content into individual knowledge_entries rows,
    then regenerates the accumulated_kb artifact.
    """
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        from app.models.artifact import Artifact
        from app.models.knowledge_entry import KnowledgeEntry

        feature = session.get(Feature, feature_id)
        if not feature:
            return

        kb_artifact = session.get(Artifact, kb_artifact_id)
        if not kb_artifact or not isinstance(kb_artifact.content, dict):
            logger.warning(
                f"KB artifact {kb_artifact_id} not found or invalid")
            return

        content = kb_artifact.content
        project_id = feature.project_id
        feature_name = content.get("feature_name", feature.description)

        # Extract decisions
        for decision in (content.get("key_decisions") or []):
            text = decision if isinstance(
                decision, str) else decision.get("decision", str(decision))
            entry = KnowledgeEntry(
                project_id=project_id,
                feature_id=feature.id,
                entry_type="decision",
                title=f"[{feature_name}] {text[:80]}",
                content=text,
                tags=[feature_name.lower().replace(" ", "-")],
                source_artifact_id=kb_artifact_id,
            )
            session.add(entry)

        # Extract architecture changes
        for change in (content.get("architecture_changes") or []):
            text = change if isinstance(
                change, str) else change.get("change", str(change))
            entry = KnowledgeEntry(
                project_id=project_id,
                feature_id=feature.id,
                entry_type="architecture_change",
                title=f"[{feature_name}] {text[:80]}",
                content=text,
                tags=[feature_name.lower().replace(" ", "-")],
                source_artifact_id=kb_artifact_id,
            )
            session.add(entry)

        # Extract lessons learned
        for lesson in (content.get("lessons_learned") or []):
            text = lesson if isinstance(
                lesson, str) else lesson.get("lesson", str(lesson))
            entry = KnowledgeEntry(
                project_id=project_id,
                feature_id=feature.id,
                entry_type="lesson",
                title=f"[{feature_name}] {text[:80]}",
                content=text,
                tags=[feature_name.lower().replace(" ", "-")],
                source_artifact_id=kb_artifact_id,
            )
            session.add(entry)

        # Extract risks as lessons
        for risk in (content.get("risks_mitigated") or []):
            text = risk if isinstance(risk, str) else str(risk)
            entry = KnowledgeEntry(
                project_id=project_id,
                feature_id=feature.id,
                entry_type="lesson",
                title=f"[{feature_name}] Risk: {text[:70]}",
                content=text,
                tags=[feature_name.lower().replace(" ", "-"), "risk"],
                source_artifact_id=kb_artifact_id,
            )
            session.add(entry)

        # Extract implementation delta items as patterns (from PR-updated KBs)
        delta = content.get("implementation_delta")
        if delta and isinstance(delta, dict):
            for deviation in (delta.get("deviated_from_plan") or []):
                entry = KnowledgeEntry(
                    project_id=project_id,
                    feature_id=feature.id,
                    entry_type="pattern",
                    title=f"[{feature_name}] Deviation: {deviation[:70]}",
                    content=deviation,
                    tags=[feature_name.lower().replace(" ", "-"), "deviation"],
                    source_artifact_id=kb_artifact_id,
                )
                session.add(entry)

        session.commit()
        logger.info(
            f"Knowledge entries extracted from KB {kb_artifact_id} for feature {feature_id}")

    except Exception as e:
        logger.exception(f"KB accumulation failed for feature {feature_id}")
    finally:
        session.close()
