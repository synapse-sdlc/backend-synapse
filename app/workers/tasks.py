import asyncio
import json
import logging

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
        _sync_engine = create_engine(sync_url)
    return Session(_sync_engine)


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
    _publish_event(f"feature:{feature_id}", event)


@celery_app.task(bind=True, name="app.workers.tasks.agent_run_task", time_limit=600, max_retries=0)
def agent_run_task(self, feature_id: str, user_message: str):
    """Run a single agent turn for a feature conversation."""
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        feature = session.get(Feature, feature_id)
        if not feature:
            logger.warning(f"Feature {feature_id} not found, skipping stale task")
            return {"skipped": True, "reason": "feature_not_found"}

        _publish_feature_event(feature_id, {"type": "thinking", "message": "Agent is processing..."})

        def on_event(event):
            _publish_feature_event(feature_id, event)

        from app.services.agent_service import run_agent_turn
        result = asyncio.run(run_agent_turn(
            feature_id=feature_id,
            user_message=user_message,
            db=session,
            on_event=on_event,
        ))

        _publish_feature_event(feature_id, {
            "type": "response",
            "content": result["final_response"],
            "phase": result["phase"],
            "artifact_id": result.get("artifact_id"),
        })
        _publish_feature_event(feature_id, {"type": "done", "phase": result["phase"]})

        return result

    except Exception as e:
        logger.exception(f"Agent turn failed for feature {feature_id}")
        _publish_feature_event(feature_id, {"type": "error", "message": str(e)})
        raise
    finally:
        session.close()


@celery_app.task(bind=True, name="app.workers.tasks.approval_agent_task", time_limit=600, max_retries=0)
def approval_agent_task(self, feature_id: str):
    """Run the next agent after an approval (plan or QA generation)."""
    session = _get_sync_session()
    try:
        from app.models.feature import Feature
        feature = session.get(Feature, feature_id)
        if not feature:
            logger.warning(f"Feature {feature_id} not found, skipping stale approval task")
            return {"skipped": True, "reason": "feature_not_found"}

        _publish_feature_event(feature_id, {"type": "thinking", "message": "Generating next artifact..."})

        def on_event(event):
            _publish_feature_event(feature_id, event)

        from app.services.agent_service import run_approval_agent
        result = asyncio.run(run_approval_agent(
            feature_id=feature_id,
            db=session,
            on_event=on_event,
        ))

        _publish_feature_event(feature_id, {
            "type": "response",
            "content": result.get("final_response", ""),
            "phase": result["phase"],
            "artifact_id": result.get("artifact_id"),
        })
        _publish_feature_event(feature_id, {"type": "done", "phase": result["phase"]})

        return result

    except Exception as e:
        logger.exception(f"Approval agent failed for feature {feature_id}")
        _publish_feature_event(feature_id, {"type": "error", "message": str(e)})
        raise
    finally:
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
        local_repo_path = download_repo_from_s3(project_id, s3_key, repo_id=repo_id)
        _publish_project_event(project_id, {
            "type": "step", "step": f"{repo.name} cloned. Running analysis...", "repo_name": repo.name,
        })

        # Step 3: Static analysis via tree-sitter
        analysis = analyze_directory(local_repo_path)
        logger.info(f"Analyzed {analysis['files_analyzed']} files for repo {repo.name}")

        # Step 4: Chunk and index (repo-scoped collection)
        _publish_project_event(project_id, {
            "type": "step", "step": f"Indexing {repo.name}...", "repo_name": repo.name,
        })
        chunks = chunk_analysis_results(analysis)
        store = VectorStore()
        store.add_chunks(chunks)
        logger.info(f"Indexed {len(chunks)} chunks for repo {repo.name}")

        # Step 5: Build context summary
        codebase_context = build_context_summary(analysis, local_repo_path)
        repo.codebase_context = codebase_context
        session.commit()

        # Step 6: Run agent to generate per-repo architecture
        _publish_project_event(project_id, {
            "type": "step", "step": f"Generating architecture for {repo.name}...", "repo_name": repo.name,
        })

        # Check if project has uploaded architecture to use as base
        uploaded_arch_context = ""
        if project and project.uploaded_architecture_id:
            uploaded_art = session.get(Artifact, project.uploaded_architecture_id)
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
        ))
        logger.info(f"Architecture generated for {repo.name} in {result['turns']} turns")

        # Step 7: Save architecture artifact to DB
        if result.get("artifact_id"):
            from pathlib import Path

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
                    type=artifact_data["type"],
                    name=artifact_data.get("name", f"Architecture: {repo.name}"),
                    content=content,
                    content_md=None,
                    parent_id=None,
                    status="approved",
                    version=1,
                    project_id=project_id,
                    repo_id=repo.id,
                )
                session.merge(db_artifact)

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
            _publish_project_event(project_id, {"type": "status", "status": "ready", "all_repos": True})
            # Trigger unified project architecture synthesis if multi-repo
            if len(all_repos) >= 2:
                synthesize_project_task.delay(project_id)

        logger.info(f"Repository {repo.name} ({repository_id}) analysis complete")

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
        except Exception:
            pass
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
        _publish_project_event(project_id, {"type": "status", "status": "analyzing"})

        github_token = None
        if project.github_token_encrypted:
            from app.utils.crypto import decrypt_token
            github_token = decrypt_token(project.github_token_encrypted)

        _publish_project_event(project_id, {"type": "step", "step": "Cloning repository..."})
        s3_key = clone_repo_to_s3(project_id, github_url, github_token=github_token)
        project.s3_repo_key = s3_key
        session.commit()

        local_repo_path = download_repo_from_s3(project_id, s3_key)
        _publish_project_event(project_id, {"type": "step", "step": "Repository cloned. Running analysis..."})

        _publish_project_event(project_id, {"type": "step", "step": "Running static analysis..."})
        analysis = analyze_directory(local_repo_path)
        logger.info(f"Analyzed {analysis['files_analyzed']} files")

        _publish_project_event(project_id, {"type": "step", "step": "Indexing codebase..."})
        chunks = chunk_analysis_results(analysis)
        store = VectorStore()
        store.add_chunks(chunks)
        logger.info(f"Indexed {len(chunks)} chunks")

        codebase_context = build_context_summary(analysis, local_repo_path)
        project.codebase_context = codebase_context
        session.commit()

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

        if result.get("artifact_id"):
            from app.models.artifact import Artifact
            from pathlib import Path

            artifact_path = Path("./artifacts") / f"{result['artifact_id']}.json"
            if artifact_path.exists():
                artifact_data = json.loads(artifact_path.read_text())
                db_artifact = Artifact(
                    id=artifact_data["id"],
                    type=artifact_data["type"],
                    name=artifact_data["name"],
                    content=json.loads(artifact_data["content"]) if isinstance(artifact_data["content"], str) else artifact_data["content"],
                    content_md=None,
                    parent_id=None,
                    status="approved",
                    version=1,
                    project_id=project_id,
                )
                session.merge(db_artifact)

        project.analysis_status = "ready"
        session.commit()
        _publish_project_event(project_id, {"type": "status", "status": "ready"})

        logger.info(f"Project {project_id} analysis complete")

    except Exception as e:
        logger.exception(f"Codebase analysis failed for project {project_id}")
        project.analysis_status = "failed"
        session.commit()
        _publish_project_event(project_id, {"type": "error", "message": str(e)})
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
            logger.warning(f"No artifacts found for feature {feature_id}, skipping KB update")
            return

        _publish_feature_event(feature_id, {"type": "thinking", "message": "Generating knowledge base entry..."})

        provider = get_provider()
        from core.orchestrator.loop import agent_loop

        kb_message = (
            f"Feature: {feature.description}\n\n"
            f"Artifact IDs:\n" + "\n".join(f"- {aid}" for aid in artifact_ids) + "\n\n"
            f"Read each artifact using get_artifact, then generate a KB entry "
            f"following the kb-update skill instructions."
        )

        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=kb_message,
            skill_name="kb-update",
        ))
        logger.info(f"KB update completed for feature {feature_id} in {result['turns']} turns")

        if result.get("artifact_id"):
            from app.models.artifact import Artifact
            from pathlib import Path

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
                    name=artifact_data.get("name", f"KB: {feature.description}"),
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
            select(JiraConfig).where(JiraConfig.project_id == feature.project_id)
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

        _publish_feature_event(feature_id, {"type": "jira_export", "step": "Starting Jira export..."})

        # Load artifacts
        spec_content = {}
        plan_content = {}
        tests_content = {}
        if feature.spec_artifact_id:
            art = session.get(Artifact, feature.spec_artifact_id)
            if art:
                spec_content = art.content if isinstance(art.content, dict) else {}
        if feature.plan_artifact_id:
            art = session.get(Artifact, feature.plan_artifact_id)
            if art:
                plan_content = art.content if isinstance(art.content, dict) else {}
        if feature.tests_artifact_id:
            art = session.get(Artifact, feature.tests_artifact_id)
            if art:
                tests_content = art.content if isinstance(art.content, dict) else {}

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
            feature_name = spec_content.get("feature_name", feature.description)
            epic_desc = spec_content.get("business_context", feature.description)
            epic = asyncio.run(svc.create_epic(project_key, feature_name, epic_desc))
            epic_key = epic["key"]
            feature.jira_epic_key = epic_key
            session.commit()

            session.add(JiraIssueLink(
                feature_id=feature_id, issue_key=epic_key, issue_type="epic",
                issue_url=epic["url"], summary=feature_name,
                source_artifact_id=feature.spec_artifact_id,
            ))
            created += 1
            _publish_feature_event(feature_id, {"type": "jira_export", "step": f"Epic created: {epic_key}"})

            for story in spec_content.get("user_stories", []):
                story_id = story.get("id", "")
                summary = f"As a {story.get('role', 'user')}, I want {story.get('action', '...')}"
                criteria = story.get("acceptance_criteria", [])
                desc_lines = []
                for ac in criteria:
                    if isinstance(ac, dict):
                        desc_lines.append(f"GIVEN {ac.get('given', '')}\nWHEN {ac.get('when', '')}\nTHEN {ac.get('then', '')}")
                    else:
                        desc_lines.append(str(ac))
                desc = "\n\n".join(desc_lines)

                result = asyncio.run(svc.create_story(project_key, summary[:250], desc, epic_key=epic_key))
                story_key_map[story_id] = result["key"]
                session.add(JiraIssueLink(
                    feature_id=feature_id, issue_key=result["key"], issue_type="story",
                    issue_url=result["url"], summary=summary[:250],
                    parent_issue_key=epic_key,
                    source_artifact_id=feature.spec_artifact_id, source_item_id=story_id,
                ))
                created += 1
                _publish_feature_event(feature_id, {"type": "jira_export", "step": f"Story created: {result['key']}"})

        # --- Subtasks (from plan) ---
        if "subtask" not in existing_types and plan_content and epic_key:
            for subtask in plan_content.get("subtasks", []):
                parent_key = story_key_map.get(subtask.get("story_id"), epic_key)
                summary = subtask.get("title", "Subtask")
                hours = subtask.get("estimated_hours", 0)
                desc = f"Estimated: {hours}h\n\n{subtask.get('description', '')}"

                result = asyncio.run(svc.create_subtask(project_key, summary[:250], desc, parent_key=parent_key))
                session.add(JiraIssueLink(
                    feature_id=feature_id, issue_key=result["key"], issue_type="subtask",
                    issue_url=result["url"], summary=summary[:250],
                    parent_issue_key=parent_key,
                    source_artifact_id=feature.plan_artifact_id, source_item_id=subtask.get("id", ""),
                ))
                created += 1
                _publish_feature_event(feature_id, {"type": "jira_export", "step": f"Subtask created: {result['key']}"})

        # --- Test cases (from tests) ---
        if "test" not in existing_types and tests_content and epic_key:
            for suite in tests_content.get("test_suites", []):
                parent_key = story_key_map.get(suite.get("story_id"), epic_key)
                for tc in suite.get("test_cases", []):
                    summary = f"[Test] {tc.get('title', 'Test case')}"
                    result = asyncio.run(svc.create_subtask(
                        project_key, summary[:250], tc.get("description", ""),
                        parent_key=parent_key,
                    ))
                    session.add(JiraIssueLink(
                        feature_id=feature_id, issue_key=result["key"], issue_type="test",
                        issue_url=result["url"], summary=summary[:250],
                        parent_issue_key=parent_key,
                        source_artifact_id=feature.tests_artifact_id, source_item_id=tc.get("id", ""),
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
        _publish_feature_event(feature_id, {"type": "error", "message": f"Jira export failed: {e}"})
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
            select(JiraConfig).where(JiraConfig.project_id == feature.project_id)
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
        _publish_feature_event(feature_id, {"type": "jira_sync", "synced": len(links)})

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
            select(Repository).where(Repository.project_id == feature.project_id)
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
                pr_data = asyncio.run(svc.get_pull_request(owner, repo, link.pr_number))
                link.state = "merged" if pr_data.get("merged_at") else pr_data.get("state", "open")
                link.merged_at = pr_data.get("merged_at")
                link.synced_at = datetime.utcnow()

                if link.state == "merged" and not link.kb_updated:
                    link.files_changed = asyncio.run(svc.get_pr_files(owner, repo, link.pr_number))
                    link.commit_messages = asyncio.run(svc.get_pr_commits(owner, repo, link.pr_number))
                    diff = asyncio.run(svc.get_pr_diff(owner, repo, link.pr_number))
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

        _publish_feature_event(feature_id, {"type": "thinking", "message": "Updating KB from merged PR..."})

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
            f"Artifact IDs:\n" + "\n".join(f"- {a}" for a in artifact_refs) + "\n\n"
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
                    name=artifact_data.get("name", f"KB (PR update): {feature.description}"),
                    content=content,
                    parent_id=feature.tests_artifact_id or feature.plan_artifact_id,
                    status="approved",
                    version=1,
                    project_id=feature.project_id,
                )
                session.merge(db_artifact)

        pr_link.kb_updated = True
        session.commit()
        _publish_feature_event(feature_id, {"type": "kb_updated", "pr_url": pr_link.pr_url})

    except Exception as e:
        logger.exception(f"PR KB update failed for feature {feature_id}")
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

        _publish_event(f"knowledge:{query_id}", {"type": "thinking", "message": "Searching knowledge base..."})

        provider = get_provider()
        from core.orchestrator.loop import agent_loop
        result = asyncio.run(agent_loop(
            provider=provider,
            user_message=f"[Persona: {persona}] Question: {question}",
            skill_name="knowledge-query",
            codebase_context=codebase_context,
        ))

        _publish_event(f"knowledge:{query_id}", {
            "type": "response",
            "content": result.get("final_response", "No answer found."),
            "done": True,
        })

    except Exception as e:
        logger.exception(f"Knowledge query failed: {question}")
        _publish_event(f"knowledge:{query_id}", {"type": "error", "message": str(e)})
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
            logger.info(f"Project {project_id} has <2 repos, skipping synthesis")
            return

        # Collect per-repo architecture artifact IDs
        arch_artifacts = session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "architecture",
            )
        ).scalars().all()

        if not arch_artifacts:
            logger.warning(f"No architecture artifacts for project {project_id}")
            return

        _publish_project_event(project_id, {"type": "step", "step": "Synthesizing cross-repo architecture..."})

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
        logger.info(f"Project synthesis completed for {project_id} in {result['turns']} turns")

        # Save project_architecture artifact to DB
        if result.get("artifact_id"):
            from pathlib import Path
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
                    name=artifact_data.get("name", f"Unified Architecture: {project.name}"),
                    content=content,
                    status="approved",
                    version=1,
                    project_id=project_id,
                )
                session.merge(db_artifact)
                session.commit()

        _publish_project_event(project_id, {"type": "status", "status": "synthesis_complete"})

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
            logger.warning(f"KB artifact {kb_artifact_id} not found or invalid")
            return

        content = kb_artifact.content
        project_id = feature.project_id
        feature_name = content.get("feature_name", feature.description)

        # Extract decisions
        for decision in (content.get("key_decisions") or []):
            text = decision if isinstance(decision, str) else decision.get("decision", str(decision))
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
            text = change if isinstance(change, str) else change.get("change", str(change))
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
            text = lesson if isinstance(lesson, str) else lesson.get("lesson", str(lesson))
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
        logger.info(f"Knowledge entries extracted from KB {kb_artifact_id} for feature {feature_id}")

    except Exception as e:
        logger.exception(f"KB accumulation failed for feature {feature_id}")
    finally:
        session.close()
