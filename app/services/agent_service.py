"""
Agent service: bridges the core orchestrator loop with the web backend.

Ports the phase transition logic from code-to-arc/repl.py to work with
database-backed state instead of in-memory ConversationSession.
"""

import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feature import Feature
from app.models.artifact import Artifact
from app.models.message import Message
from app.models.repository import Repository
from app.models.project import Project
from app.config import get_provider, settings
from app.services.context_builder import build_agent_context

logger = logging.getLogger(__name__)

PHASE_SKILL_MAP = {
    "gathering": "spec-drafting",
    "spec_review": "spec-drafting",
    "plan_review": "tech-planning",
    "qa_review": "qa-testing",
}

CONVERSATIONAL_PHASES = {"gathering",
                         "spec_review", "plan_review", "qa_review"}

# Maps artifact type to the phase it triggers
ARTIFACT_PHASE_MAP = {
    "spec": "spec_review",
    "plan": "plan_review",
    "tests": "qa_review",
}

# Maps current phase to the artifact field on Feature
PHASE_ARTIFACT_FIELD = {
    "spec_review": "spec_artifact_id",
    "plan_review": "plan_artifact_id",
    "qa_review": "tests_artifact_id",
}

# Maps phase to the next phase after approval
NEXT_PHASE = {
    "spec_review": "plan_review",
    "plan_review": "qa_review",
    "qa_review": "done",
}

# Maps phase to the approval message that triggers the next agent
APPROVAL_MESSAGES = {
    "spec_review": (
        "The Product Owner has approved the spec (artifact ID: {artifact_id}). "
        "Now generate a detailed technical implementation plan. "
        "Read the approved spec using get_artifact, then follow the tech-planning skill instructions. "
        "Store the plan using store_artifact with type='plan' and parent_id='{artifact_id}'."
    ),
    "plan_review": (
        "The Tech Lead has approved the technical plan (artifact ID: {artifact_id}). "
        "The feature spec is artifact ID: {spec_id}. "
        "Now generate comprehensive QA test cases. "
        "Read both the spec and plan using get_artifact, then follow the qa-testing skill instructions. "
        "Store the test cases using store_artifact with type='tests' and parent_id='{artifact_id}'."
    ),
}


def load_conversation_history(db: Session, feature_id: str, max_messages: int = 80) -> list:
    """Load messages from DB with smart truncation.

    Loads the most recent max_messages. If history exceeds this, older messages
    are summarized into a single context message so the agent retains awareness
    of what happened without full context bloat.
    """
    all_messages = (
        db.query(Message)
        .filter(Message.feature_id == feature_id)
        .order_by(Message.created_at)
        .all()
    )

    # Convert all to agent_loop format
    full_history = []
    for m in all_messages:
        entry = {"role": m.role, "content": m.content or ""}
        if m.tool_name:
            entry["tool_name"] = m.tool_name
        if m.tool_calls:
            entry["tool_calls"] = m.tool_calls
        full_history.append(entry)

    if len(full_history) <= max_messages:
        return full_history

    # Summarize older messages, keep recent ones in full
    older = full_history[:-max_messages]
    recent = full_history[-max_messages:]

    # Build a concise summary of the older conversation
    summary_parts = []
    for m in older:
        if m["role"] == "user":
            content = (m["content"] or "")[:150]
            if content:
                summary_parts.append(f"User: {content}")
        elif m["role"] == "assistant":
            content = (m["content"] or "")[:150]
            if content:
                summary_parts.append(f"Agent: {content}")
        elif m["role"] == "tool" and m.get("tool_name") == "store_artifact":
            try:
                data = json.loads(m["content"])
                summary_parts.append(
                    f"Stored artifact: {data.get('artifact_id', '?')}")
            except (json.JSONDecodeError, TypeError):
                pass

    summary_text = (
        f"[Earlier conversation summary — {len(older)} messages omitted for context budget]\n"
        + "\n".join(summary_parts[-20:])  # Keep last 20 summary lines
        if summary_parts else f"[{len(older)} earlier messages omitted]"
    )

    # Prepend summary as a system-style user message
    return [{"role": "user", "content": summary_text}] + recent


def save_new_messages(db: Session, feature_id: str, old_count: int, messages: list):
    """Save only the new messages (those added during the agent turn) to DB."""
    new_messages = messages[old_count:]
    for m in new_messages:
        db_msg = Message(
            feature_id=feature_id,
            role=m["role"],
            content=m.get("content", ""),
            tool_name=m.get("tool_name"),
            tool_calls=m.get("tool_calls"),
        )
        db.add(db_msg)
    db.commit()


def check_for_new_artifacts(db: Session, feature: Feature, messages: list) -> Optional[str]:
    """Check if the agent stored any new artifacts and update feature state.

    Ported from code-to-arc/repl.py _check_for_new_artifacts().
    Returns the new artifact ID if found, None otherwise.
    """
    from pathlib import Path

    for m in reversed(messages):
        if m.get("role") != "tool" or m.get("tool_name") != "store_artifact":
            continue
        try:
            data = json.loads(m["content"])
            aid = data.get("artifact_id")
            if not aid:
                continue

            # Check what type of artifact was stored by reading from filesystem
            # (the core tool writes to ./artifacts/)
            # Check project-scoped dir first, flat fallback
            artifact_path = Path("./artifacts") / str(feature.project_id) / f"{aid}.json"
            if not artifact_path.exists():
                artifact_path = Path("./artifacts") / f"{aid}.json"
            if not artifact_path.exists():
                continue

            artifact_data = json.loads(artifact_path.read_text())
            art_type = artifact_data.get("type")

            # Save artifact to DB
            content = artifact_data.get("content", "")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    content = {"raw": content}

            new_version = artifact_data.get("version", 1)

            # If this is an in-place update (same ID, bumped version), snapshot the old version first
            existing = db.get(Artifact, aid)
            prev_id = None
            if existing and new_version > 1 and existing.version < new_version:
                import hashlib as _hl
                from datetime import datetime as _dt
                snapshot_id = _hl.sha256(f"{aid}:v{existing.version}:{_dt.now().isoformat()}".encode()).hexdigest()[:12]
                snapshot = Artifact(
                    id=snapshot_id,
                    type=existing.type,
                    name=existing.name,
                    content=existing.content,
                    parent_id=existing.parent_id,
                    status="superseded",
                    version=existing.version,
                    feature_id=existing.feature_id,
                    project_id=existing.project_id,
                    confidence_score=existing.confidence_score,
                )
                db.add(snapshot)
                prev_id = snapshot_id

            db_artifact = Artifact(
                id=aid,
                type=art_type,
                name=artifact_data.get("name", ""),
                content=content,
                parent_id=artifact_data.get("parent_id"),
                status=artifact_data.get("status", "draft"),
                version=new_version,
                feature_id=feature.id,
                project_id=feature.project_id,
                confidence_score=artifact_data.get("confidence_score"),
                previous_version_id=prev_id,
            )
            db.merge(db_artifact)

            # Update feature phase based on artifact type
            # Link previous version when replacing an existing artifact
            if art_type == "spec" and feature.spec_artifact_id and feature.spec_artifact_id != aid:
                db_artifact.previous_version_id = feature.spec_artifact_id
                old = db.get(Artifact, feature.spec_artifact_id)
                if old:
                    old.status = "superseded"
            if art_type == "plan" and feature.plan_artifact_id and feature.plan_artifact_id != aid:
                db_artifact.previous_version_id = feature.plan_artifact_id
                old = db.get(Artifact, feature.plan_artifact_id)
                if old:
                    old.status = "superseded"
            if art_type == "tests" and feature.tests_artifact_id and feature.tests_artifact_id != aid:
                db_artifact.previous_version_id = feature.tests_artifact_id
                old = db.get(Artifact, feature.tests_artifact_id)
                if old:
                    old.status = "superseded"

            if art_type == "spec":
                feature.spec_artifact_id = aid
                if feature.phase == "gathering":
                    feature.phase = "spec_review"
                    logger.info(
                        f"Feature {feature.id}: spec generated, moving to spec_review")
                db.commit()
                return aid

            if art_type == "plan":
                feature.plan_artifact_id = aid
                if feature.phase in ("spec_review", "plan_review"):
                    feature.phase = "plan_review"
                    logger.info(
                        f"Feature {feature.id}: plan generated, moving to plan_review")
                db.commit()
                return aid

            if art_type == "tests":
                feature.tests_artifact_id = aid
                if feature.phase in ("plan_review", "qa_review"):
                    feature.phase = "qa_review"
                    logger.info(
                        f"Feature {feature.id}: tests generated, moving to qa_review")
                db.commit()
                return aid

            if art_type == "scaffold":
                feature.scaffold_artifact_id = aid
                logger.info(f"Feature {feature.id}: scaffold generated")
                db.commit()
                return aid

        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse artifact from message: {e}")
            continue

    return None


async def run_agent_turn(
    feature_id: str,
    user_message: str,
    db: Session,
    on_event: callable = None,
    model_tier: str = "balanced",
) -> dict:
    """Run a single agent turn for a feature conversation.

    This is the core function that bridges the web backend with the orchestrator.
    """
    feature = db.get(Feature, feature_id)
    if not feature:
        raise ValueError(f"Feature {feature_id} not found")

    # Load conversation history from DB
    history = load_conversation_history(db, str(feature_id))
    old_count = len(history)

    # Select skill based on current phase
    skill = PHASE_SKILL_MAP.get(feature.phase, "spec-drafting")
    stop_on_text = feature.phase in CONVERSATIONAL_PHASES

    # Get provider (Ollama or Bedrock)
    provider = get_provider(model_tier=model_tier)

    # Load rich multi-layered context (repos + architecture + knowledge + config)
    codebase_context = build_agent_context(db, feature)

    # Set search context for scoped codebase search
    from core.tools.codebase.search_codebase import SearchCodebaseTool
    repos = db.execute(
        select(Repository).where(Repository.project_id == feature.project_id)
    ).scalars().all()
    repo_ids = [str(r.id) for r in repos]
    SearchCodebaseTool.set_context(project_id=str(feature.project_id), repo_ids=repo_ids)

    # Ensure repos are on local disk (sync from S3 if missing after restart/deploy)
    from pathlib import Path as _Path
    for r in repos:
        repo_path = _Path(settings.local_repos_dir) / str(feature.project_id) / str(r.id) / "repo"
        if not repo_path.exists() and r.s3_repo_key:
            try:
                from app.services.project_service import download_repo_from_s3
                download_repo_from_s3(str(feature.project_id), r.s3_repo_key, str(r.id))
                logger.info(f"Synced repo {r.name} from S3 for agent access")
            except Exception as e:
                logger.warning(f"Failed to sync repo {r.name} from S3: {e}")

    # Set file tool sandbox — restrict to user's repos only
    from core.tools.sandbox import set_sandbox
    from core.tools.artifacts.store_artifact import StoreArtifactTool
    from core.tools.artifacts.get_artifact import GetArtifactTool

    sandbox_roots = []
    for r in repos:
        repo_path = _Path(settings.local_repos_dir) / str(feature.project_id) / str(r.id) / "repo"
        if repo_path.exists():
            sandbox_roots.append(str(repo_path))
    # Also allow project-scoped artifacts
    sandbox_roots.append(str((_Path("./artifacts") / str(feature.project_id)).resolve()))
    # Also allow flat artifacts dir (backward compat)
    sandbox_roots.append(str(_Path("./artifacts").resolve()))
    set_sandbox(sandbox_roots)

    # Set artifact context for project-scoped storage
    StoreArtifactTool.set_context(project_id=str(feature.project_id))
    GetArtifactTool.set_context(project_id=str(feature.project_id))

    # Load project-level custom skills
    project = db.get(Project, feature.project_id)
    project_custom_skills = (project.custom_skills or {}) if project else {}

    # Run the core agent loop
    from core.orchestrator.loop import agent_loop
    result = await agent_loop(
        provider=provider,
        user_message=user_message,
        skill_name=skill,
        codebase_context=codebase_context,
        conversation_history=history if history else None,
        stop_on_text=stop_on_text,
        on_event=on_event,
        custom_skills=project_custom_skills,
        # Observability: group all turns for this feature into one Langfuse session
        trace_session_id=str(feature_id),
        trace_user_id=str(project.org_id) if project else None,
        trace_metadata={
            "feature_id": str(feature_id),
            "project_id": str(feature.project_id),
            "phase": feature.phase,
            "skill": skill,
        },
    )

    # Save new messages to DB
    save_new_messages(db, str(feature_id), old_count, result["messages"])

    # Check if agent stored any artifacts, update feature phase
    new_artifact_id = check_for_new_artifacts(db, feature, result["messages"])

    return {
        "final_response": result["final_response"],
        "turns": result["turns"],
        "artifact_id": new_artifact_id or result.get("artifact_id"),
        "phase": feature.phase,
    }


async def run_approval_agent(
    feature_id: str,
    db: Session,
    on_event: callable = None,
    model_tier: str = "balanced",
) -> dict:
    """Run the next agent after an approval (plan generation or QA test generation).

    Called when a user approves spec -> triggers plan agent,
    or approves plan -> triggers QA agent.
    """
    feature = db.get(Feature, feature_id)
    if not feature:
        raise ValueError(f"Feature {feature_id} not found")

    phase = feature.phase

    # Determine the approval message
    if phase == "plan_review":
        # Spec was just approved, generate plan
        msg = APPROVAL_MESSAGES["spec_review"].format(
            artifact_id=feature.spec_artifact_id
        )
        skill = "tech-planning"
    elif phase == "qa_review":
        # Plan was just approved, generate tests
        msg = APPROVAL_MESSAGES["plan_review"].format(
            artifact_id=feature.plan_artifact_id,
            spec_id=feature.spec_artifact_id,
        )
        skill = "qa-testing"
    else:
        logger.warning(f"No approval agent for phase: {phase}")
        return {"phase": phase}

    # Load conversation history
    history = load_conversation_history(db, str(feature_id))
    old_count = len(history)

    provider = get_provider(model_tier=model_tier)

    # Load rich multi-layered context (repos + architecture + knowledge + config)
    codebase_context = build_agent_context(db, feature)

    # Load project-level custom skills
    project = db.get(Project, feature.project_id)
    project_custom_skills = (project.custom_skills or {}) if project else {}

    from core.orchestrator.loop import agent_loop
    result = await agent_loop(
        provider=provider,
        user_message=msg,
        skill_name=skill,
        codebase_context=codebase_context,
        conversation_history=history if history else None,
        stop_on_text=False,  # Don't stop on text, we want the full artifact
        on_event=on_event,
        custom_skills=project_custom_skills,
        # Observability: group all turns for this feature into one Langfuse session
        trace_session_id=str(feature_id),
        trace_user_id=str(project.org_id) if project else None,
        trace_metadata={
            "feature_id": str(feature_id),
            "project_id": str(feature.project_id),
            "phase": feature.phase,
            "skill": skill,
            "trigger": "approval",
        },
    )

    save_new_messages(db, str(feature_id), old_count, result["messages"])
    new_artifact_id = check_for_new_artifacts(db, feature, result["messages"])

    return {
        "final_response": result["final_response"],
        "turns": result["turns"],
        "artifact_id": new_artifact_id or result.get("artifact_id"),
        "phase": feature.phase,
    }


async def run_scaffold_agent(
    feature_id: str,
    db: Session,
    model_tier: str = "balanced",
    on_event: callable = None,
) -> dict:
    """Generate code scaffolds from the approved plan, spec, and tests."""
    feature = db.get(Feature, feature_id)
    if not feature:
        raise ValueError(f"Feature {feature_id} not found")

    if not feature.plan_artifact_id:
        raise ValueError("Plan artifact required for scaffold generation")

    # Build the instruction message
    parts = [f"Generate code scaffolds for this feature."]
    parts.append(f"Plan artifact ID: {feature.plan_artifact_id}")
    if feature.spec_artifact_id:
        parts.append(f"Spec artifact ID: {feature.spec_artifact_id}")
    if feature.tests_artifact_id:
        parts.append(f"Tests artifact ID: {feature.tests_artifact_id}")
    parts.append(f"Read all artifacts using get_artifact, then follow the code-scaffold skill instructions.")
    parts.append(f"Store the scaffold using store_artifact with type='scaffold' and parent_id='{feature.plan_artifact_id}'.")
    msg = "\n".join(parts)

    history = load_conversation_history(db, str(feature_id))
    old_count = len(history)

    provider = get_provider(model_tier=model_tier)
    codebase_context = build_agent_context(db, feature)

    project = db.get(Project, feature.project_id)
    project_custom_skills = (project.custom_skills or {}) if project else {}

    from core.orchestrator.loop import agent_loop
    result = await agent_loop(
        provider=provider,
        user_message=msg,
        skill_name="code-scaffold",
        codebase_context=codebase_context,
        conversation_history=history if history else None,
        stop_on_text=False,
        on_event=on_event,
        custom_skills=project_custom_skills,
    )

    save_new_messages(db, str(feature_id), old_count, result["messages"])
    new_artifact_id = check_for_new_artifacts(db, feature, result["messages"])

    # Link scaffold artifact to feature
    if new_artifact_id:
        feature.scaffold_artifact_id = new_artifact_id
        db.commit()

    return {
        "final_response": result["final_response"],
        "turns": result["turns"],
        "artifact_id": new_artifact_id or result.get("artifact_id"),
        "phase": feature.phase,
    }
