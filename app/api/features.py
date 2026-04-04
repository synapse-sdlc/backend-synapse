import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.feature import Feature
from app.models.project import Project
from app.models.artifact import Artifact
from app.models.message import Message
from app.schemas.feature import (
    FeatureCreate, FeatureResponse, MessageRequest, MessageResponse, RejectRequest,
)
from app.deps import get_current_user, get_optional_user, CurrentUser
from app.models.jira_config import JiraConfig

router = APIRouter()


def _verify_feature_access(db: Session, feature_id: UUID, user: CurrentUser) -> Feature:
    """Verify feature exists and belongs to user's org."""
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    project = db.get(Project, feature.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Feature not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Feature not found")
    return feature


def _verify_project_access(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    """Verify project exists and belongs to user's org."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _resolve_model_tier(db: Session, feature: Feature) -> str:
    """Get model tier from project config, defaulting to 'balanced'."""
    valid_tiers = {"fast", "balanced", "powerful"}
    project = db.get(Project, feature.project_id)
    if project and project.config:
        tier = project.config.get("model_tier", "balanced")
        return tier if tier in valid_tiers else "balanced"
    return "balanced"


def _check_agent_not_running(feature: Feature):
    """Check if an agent task is already running for this feature. Raises 409 if so."""
    if feature.agent_task_id:
        from celery.result import AsyncResult
        from app.workers.celery_app import celery_app
        result = AsyncResult(feature.agent_task_id, app=celery_app)
        if result.state in ("PENDING", "STARTED", "RETRY"):
            raise HTTPException(
                status_code=409, detail="Agent is already processing this feature. Please wait.")
        # Task completed/failed — stale ID, will be overwritten


def _record_task_id(db: Session, feature: Feature, task_id: str):
    """Record Celery task ID on feature for concurrency tracking."""
    feature.agent_task_id = task_id
    db.commit()


def _try_auto_jira_export(db: Session, feature: Feature):
    """Auto-trigger Jira export if configured. Fails silently — never breaks approval."""
    try:
        project = db.get(Project, feature.project_id)
        if not project:
            return
        config = project.config or {}
        if not config.get("auto_export_jira", True):
            return
        jira_config = db.execute(
            select(JiraConfig).where(
                JiraConfig.project_id == feature.project_id)
        ).scalars().first()
        if not jira_config:
            return
        from app.workers.tasks import jira_export_task
        jira_export_task.delay(str(feature.id), None)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"Auto Jira export failed to queue: {e}")


@router.post("/projects/{project_id}/features", response_model=FeatureResponse, status_code=201)
def create_feature(
    project_id: UUID,
    body: FeatureCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project_access(db, project_id, user)

    feature = Feature(project_id=project_id, description=body.description)
    db.add(feature)
    db.commit()
    db.refresh(feature)

    from app.workers.tasks import agent_run_task
    initial_message = (
        f'A Product Owner wants to draft a feature spec for:\n\n'
        f'"{body.description}"\n\n'
        f'Follow the spec-drafting skill instructions. Start with Phase 1: '
        f'ask 3-5 clarifying questions before generating anything.'
    )
    model_tier = _resolve_model_tier(db, feature)
    task = agent_run_task.delay(str(feature.id), initial_message, model_tier)
    _record_task_id(db, feature, task.id)

    return feature


@router.get("/features/{feature_id}", response_model=FeatureResponse)
def get_feature(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _verify_feature_access(db, feature_id, user)


@router.post("/features/{feature_id}/message", status_code=202)
def send_message(
    feature_id: UUID,
    body: MessageRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)

    if feature.phase == "closed":
        raise HTTPException(status_code=400, detail="Feature is closed")

    _check_agent_not_running(feature)

    db_msg = Message(
        feature_id=feature_id,
        role="user",
        content=body.content,
        user_id=user.id,
        user_name=user.name or "User",
    )
    db.add(db_msg)
    db.commit()

    model_tier = _resolve_model_tier(db, feature)
    from app.workers.tasks import agent_run_task
    task = agent_run_task.delay(str(feature_id), body.content, model_tier)
    _record_task_id(db, feature, task.id)

    return {"status": "accepted", "feature_id": str(feature_id)}


@router.post("/features/{feature_id}/approve", status_code=200)
def approve_feature(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)

    phase = feature.phase
    from app.workers.tasks import approval_agent_task

    phase_artifact_map = {
        "spec_review": ("spec", feature.spec_artifact_id),
        "plan_review": ("plan", feature.plan_artifact_id),
        "qa_review": ("tests", feature.tests_artifact_id),
    }

    if phase not in phase_artifact_map:
        raise HTTPException(
            status_code=400, detail=f"Cannot approve in phase: {phase}")

    artifact_type, artifact_id = phase_artifact_map[phase]
    if not artifact_id:
        raise HTTPException(
            status_code=400, detail=f"No {artifact_type} artifact to approve")

    # Atomic phase transition — prevents concurrent duplicate approvals
    next_phase = {"spec_review": "plan_review",
                  "plan_review": "qa_review", "qa_review": "done"}
    result = db.execute(
        update(Feature)
        .where(Feature.id == feature_id, Feature.phase == phase)
        .values(phase=next_phase[phase])
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=409, detail="Feature was already updated by another request")

    # Mark artifact as approved
    artifact = db.get(Artifact, artifact_id)
    if artifact:
        artifact.status = "approved"

    approval_msg = Message(
        feature_id=feature_id,
        role="user",
        content=f"{user.name or 'User'} approved the {artifact_type}.",
        user_id=user.id,
        user_name=user.name or "User",
    )
    db.add(approval_msg)
    db.commit()

    # Trigger next agent (except when moving to done)
    if next_phase[phase] != "done":
        model_tier = _resolve_model_tier(db, feature)
        task = approval_agent_task.delay(str(feature_id), model_tier)
        _record_task_id(db, feature, task.id)
    else:
        # Phase just moved to "done" — auto-export to Jira if configured
        _try_auto_jira_export(db, feature)

    db.refresh(feature)
    return {"status": "approved", "phase": feature.phase, "feature_id": str(feature_id)}


@router.post("/features/{feature_id}/reject", status_code=200)
def reject_artifact(
    feature_id: UUID,
    body: RejectRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)

    phase = feature.phase

    reject_map = {
        "spec_review": ("gathering", "spec_artifact_id", "spec"),
        "plan_review": ("spec_review", "plan_artifact_id", "plan"),
        "qa_review": ("plan_review", "tests_artifact_id", "tests"),
    }

    if phase not in reject_map:
        raise HTTPException(
            status_code=400, detail=f"Cannot reject in phase: {phase}")

    prev_phase, artifact_field, artifact_type = reject_map[phase]

    artifact_id = getattr(feature, artifact_field)
    if artifact_id:
        artifact = db.get(Artifact, artifact_id)
        if artifact:
            artifact.status = "superseded"
        setattr(feature, artifact_field, None)

    feature.phase = prev_phase

    reject_msg = Message(
        feature_id=feature_id,
        role="user",
        content=f"{user.name or 'User'} requested changes to the {artifact_type}: {body.reason}",
        user_id=user.id,
        user_name=user.name or "User",
    )
    db.add(reject_msg)
    db.commit()

    from app.workers.tasks import agent_run_task
    revision_prompt = (
        f"The reviewer has requested changes to the {artifact_type}. "
        f"Feedback: {body.reason}\n\n"
        f"Please revise the {artifact_type} based on this feedback."
    )
    model_tier = _resolve_model_tier(db, feature)
    task = agent_run_task.delay(str(feature_id), revision_prompt, model_tier)
    _record_task_id(db, feature, task.id)

    db.refresh(feature)
    return {"status": "rejected", "phase": feature.phase, "feature_id": str(feature_id)}


@router.post("/features/{feature_id}/rollback", status_code=200)
def rollback_feature(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Roll back to the previous agent phase without triggering revision."""
    feature = _verify_feature_access(db, feature_id, user)

    phase = feature.phase

    # Maps current phase → (previous phase, artifact field to clear, previous artifact field to set to draft)
    rollback_map = {
        "qa_review":   ("plan_review", "tests_artifact_id", "plan_artifact_id"),
        "plan_review": ("spec_review",  "plan_artifact_id",  "spec_artifact_id"),
    }

    if phase not in rollback_map:
        raise HTTPException(status_code=400, detail=f"Cannot roll back from phase: {phase}")

    prev_phase, current_artifact_field, prev_artifact_field = rollback_map[phase]

    # Mark current phase's artifact as rolled_back and detach it
    current_artifact_id = getattr(feature, current_artifact_field)
    if current_artifact_id:
        artifact = db.get(Artifact, current_artifact_id)
        if artifact:
            artifact.status = "rolled_back"
        setattr(feature, current_artifact_field, None)

    # Set previous phase's artifact back to draft so user must re-approve
    prev_artifact_id = getattr(feature, prev_artifact_field)
    if prev_artifact_id:
        prev_artifact = db.get(Artifact, prev_artifact_id)
        if prev_artifact:
            prev_artifact.status = "draft"

    feature.phase = prev_phase

    phase_label = {"plan_review": "tech plan review", "spec_review": "spec review"}
    rollback_msg = Message(
        feature_id=feature_id,
        role="user",
        content=f"{user.name or 'User'} rolled back to {phase_label.get(prev_phase, prev_phase)}.",
        user_id=user.id,
        user_name=user.name or "User",
    )
    db.add(rollback_msg)
    db.commit()
    db.refresh(feature)

    return {"status": "rolled_back", "phase": feature.phase, "feature_id": str(feature_id)}


@router.post("/features/{feature_id}/close", status_code=200)
def close_feature(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)

    if feature.phase != "done":
        raise HTTPException(
            status_code=400, detail="Can only close features in 'done' phase")

    feature.phase = "closed"

    close_msg = Message(
        feature_id=feature_id,
        role="user",
        content=f"{user.name or 'User'} closed the feature.",
        user_id=user.id,
        user_name=user.name or "User",
    )
    db.add(close_msg)
    db.commit()

    try:
        from app.workers.tasks import kb_update_task
        kb_update_task.delay(str(feature_id))
    except (ConnectionError, ImportError) as e:
        import logging
        logging.getLogger(__name__).warning(
            f"KB update task failed to queue: {e}")

    db.refresh(feature)
    return {"status": "closed", "phase": feature.phase, "feature_id": str(feature_id)}


@router.get("/projects/{project_id}/features", response_model=list[FeatureResponse])
def list_features(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    _verify_project_access(db, project_id, user)
    result = db.execute(
        select(Feature)
        .where(Feature.project_id == project_id)
        .order_by(Feature.created_at.desc())
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/features/{feature_id}/messages", response_model=list[MessageResponse])
def list_messages(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    _verify_feature_access(db, feature_id, user)
    result = db.execute(
        select(Message)
        .where(Message.feature_id == feature_id)
        .where(Message.role.in_(["user", "assistant"]))
        .order_by(Message.created_at)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/features/{feature_id}/jira-preview")
def jira_preview(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)

    spec_data = {}
    plan_data = {}
    tests_data = {}

    if feature.spec_artifact_id:
        spec_art = db.get(Artifact, feature.spec_artifact_id)
        if spec_art:
            spec_data = spec_art.content if isinstance(
                spec_art.content, dict) else {}

    if feature.plan_artifact_id:
        plan_art = db.get(Artifact, feature.plan_artifact_id)
        if plan_art:
            plan_data = plan_art.content if isinstance(
                plan_art.content, dict) else {}

    if feature.tests_artifact_id:
        tests_art = db.get(Artifact, feature.tests_artifact_id)
        if tests_art:
            tests_data = tests_art.content if isinstance(
                tests_art.content, dict) else {}

    feature_name = spec_data.get("feature_name") or plan_data.get(
        "feature_name", feature.description)
    user_stories = spec_data.get("user_stories", [])
    subtasks = plan_data.get("subtasks", [])
    test_suites = tests_data.get("test_suites", [])

    def safe_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0

    total_hours = sum(safe_float(t.get("estimated_hours", 0))
                      for t in subtasks if isinstance(t, dict))
    total_tests = sum(len(s.get("test_cases", s.get("tests", [])))
                      for s in test_suites if isinstance(s, dict))

    return {
        "feature_name": feature_name,
        "priority": spec_data.get("priority", "P1"),
        "stories": len(user_stories),
        "tasks": len(subtasks),
        "test_cases": total_tests,
        "estimated_human_hours": total_hours,
        "estimated_ai_hours": round(total_hours * 0.4, 1),
        "user_stories": user_stories,
        "subtasks": subtasks,
        "test_suites": test_suites,
    }


@router.get("/features/{feature_id}/tests/export")
def export_tests_csv(
    feature_id: UUID,
    token: str = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_optional_user),
):
    """Export test cases as CSV download. Accepts ?token= query param for browser downloads."""
    from app.deps import get_optional_user as _  # noqa
    # If no auth header (browser download), try query param token
    if not user and token:
        from app.deps import _resolve_token
        payload = _resolve_token(token, db)
        if payload:
            from app.deps import CurrentUser as CU
            user = CU(id=UUID(payload["sub"]), org_id=UUID(payload["org_id"]), role=payload.get(
                "role", "admin"), name=payload.get("name", ""))
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    feature = _verify_feature_access(db, feature_id, user)

    if not feature.tests_artifact_id:
        raise HTTPException(
            status_code=404, detail="No test cases generated yet")

    art = db.get(Artifact, feature.tests_artifact_id)
    if not art:
        raise HTTPException(status_code=404, detail="Tests artifact not found")

    content = art.content if isinstance(art.content, dict) else {}
    test_suites = content.get("test_suites", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Suite", "Type", "Story", "ID", "Title", "Priority",
                    "Preconditions", "Steps", "Expected Result", "Automated"])

    for suite in test_suites:
        if not isinstance(suite, dict):
            continue
        suite_name = suite.get("name", "")
        suite_type = suite.get("type", "")
        story_id = suite.get("story_id", "")

        for tc in suite.get("test_cases", []):
            if not isinstance(tc, dict):
                continue
            preconditions = "; ".join(tc.get("preconditions", [])) if isinstance(
                tc.get("preconditions"), list) else str(tc.get("preconditions", ""))
            steps = "; ".join(tc.get("steps", [])) if isinstance(
                tc.get("steps"), list) else str(tc.get("steps", ""))

            writer.writerow([
                suite_name,
                suite_type,
                story_id,
                tc.get("id", ""),
                tc.get("title", ""),
                tc.get("priority", ""),
                preconditions,
                steps,
                tc.get("expected_result", ""),
                "Yes" if tc.get("automated") else "No",
            ])

    output.seek(0)
    feature_name = content.get("feature_name", feature.description)[
        :50].replace(" ", "_")
    filename = f"test_cases_{feature_name}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/features/{feature_id}/traceability")
def get_traceability_report(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Analyze traceability gaps across spec -> plan -> tests artifact chain."""
    feature = _verify_feature_access(db, feature_id, user)

    spec = db.get(
        Artifact, feature.spec_artifact_id) if feature.spec_artifact_id else None
    plan = db.get(
        Artifact, feature.plan_artifact_id) if feature.plan_artifact_id else None
    tests = db.get(
        Artifact, feature.tests_artifact_id) if feature.tests_artifact_id else None

    if not all([spec, plan, tests]):
        missing = []
        if not spec:
            missing.append("spec")
        if not plan:
            missing.append("plan")
        if not tests:
            missing.append("tests")
        return {"status": "incomplete", "missing": missing, "message": f"Missing artifacts: {', '.join(missing)}"}

    from app.services.traceability_service import detect_gaps
    report = detect_gaps(
        spec.content if isinstance(spec.content, dict) else {},
        plan.content if isinstance(plan.content, dict) else {},
        tests.content if isinstance(tests.content, dict) else {},
    )
    report["status"] = "complete"
    return report


@router.post("/features/{feature_id}/generate-scaffold", status_code=202)
def generate_scaffold(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Generate code scaffold files from the approved plan."""
    feature = _verify_feature_access(db, feature_id, user)

    if not feature.plan_artifact_id:
        raise HTTPException(
            status_code=400, detail="Plan artifact required — approve the spec first")

    _check_agent_not_running(feature)

    model_tier = _resolve_model_tier(db, feature)
    from app.workers.tasks import scaffold_generation_task
    task = scaffold_generation_task.delay(str(feature_id), model_tier)

    feature.agent_task_id = task.id
    db.commit()

    return {"status": "accepted", "task_id": task.id}


@router.get("/features/{feature_id}/task-prompts")
def get_task_prompts(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Generate AI coding prompts for all subtasks in the plan."""
    feature = _verify_feature_access(db, feature_id, user)

    if not feature.plan_artifact_id:
        raise HTTPException(status_code=400, detail="Plan artifact required")

    spec_a = db.get(
        Artifact, feature.spec_artifact_id) if feature.spec_artifact_id else None
    plan_a = db.get(Artifact, feature.plan_artifact_id)
    tests_a = db.get(
        Artifact, feature.tests_artifact_id) if feature.tests_artifact_id else None
    scaffold_a = db.get(
        Artifact, feature.scaffold_artifact_id) if feature.scaffold_artifact_id else None

    spec_c = spec_a.content if spec_a and isinstance(
        spec_a.content, dict) else {}
    plan_c = plan_a.content if plan_a and isinstance(
        plan_a.content, dict) else {}
    tests_c = tests_a.content if tests_a and isinstance(
        tests_a.content, dict) else {}
    scaffold_c = scaffold_a.content if scaffold_a and isinstance(
        scaffold_a.content, dict) else {}

    # Load knowledge entries
    from app.models.knowledge_entry import KnowledgeEntry
    kb_entries = db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.project_id == feature.project_id,
            KnowledgeEntry.entry_type.in_(["pattern", "decision"]),
        ).limit(10)
    ).scalars().all()

    # Get repo info
    from app.models.repository import Repository
    repo = db.execute(
        select(Repository).where(
            Repository.project_id == feature.project_id).limit(1)
    ).scalars().first()

    from app.services.prompt_builder import build_all_task_prompts
    prompts = build_all_task_prompts(
        spec_content=spec_c,
        plan_content=plan_c,
        tests_content=tests_c,
        scaffold_content=scaffold_c,
        knowledge_entries=kb_entries,
        repo_name=repo.name if repo else "",
        repo_type=repo.repo_type if repo else "",
        feature_name=spec_c.get("feature_name", feature.description),
    )

    return {"feature_name": spec_c.get("feature_name", feature.description), "prompts": prompts}


@router.get("/features/{feature_id}/task-prompts/{subtask_id}")
def get_task_prompt(
    feature_id: UUID,
    subtask_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Generate AI coding prompt for a single subtask."""
    result = get_task_prompts(feature_id, db, user)
    for p in result["prompts"]:
        if p["subtask_id"] == subtask_id:
            return p
    raise HTTPException(
        status_code=404, detail=f"Subtask {subtask_id} not found")


def _load_export_data(db, feature):
    """Load all artifacts + knowledge + traceability for export."""
    spec_a = db.get(
        Artifact, feature.spec_artifact_id) if feature.spec_artifact_id else None
    plan_a = db.get(
        Artifact, feature.plan_artifact_id) if feature.plan_artifact_id else None
    tests_a = db.get(
        Artifact, feature.tests_artifact_id) if feature.tests_artifact_id else None
    scaffold_a = db.get(
        Artifact, feature.scaffold_artifact_id) if feature.scaffold_artifact_id else None

    spec_c = spec_a.content if spec_a and isinstance(
        spec_a.content, dict) else {}
    plan_c = plan_a.content if plan_a and isinstance(
        plan_a.content, dict) else {}
    tests_c = tests_a.content if tests_a and isinstance(
        tests_a.content, dict) else {}
    scaffold_c = scaffold_a.content if scaffold_a and isinstance(
        scaffold_a.content, dict) else {}

    from app.models.knowledge_entry import KnowledgeEntry
    kb_entries = db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.project_id == feature.project_id).limit(50)
    ).scalars().all()

    trace = None
    if spec_c and plan_c and tests_c:
        from app.services.traceability_service import detect_gaps
        trace = detect_gaps(spec_c, plan_c, tests_c)
        trace["status"] = "complete"

    return spec_c, plan_c, tests_c, scaffold_c, kb_entries, trace


@router.get("/features/{feature_id}/export/xlsx")
def export_xlsx(
    feature_id: UUID,
    token: str = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_optional_user),
):
    """Export feature as multi-sheet Excel workbook."""
    if not user and token:
        from app.deps import _resolve_token
        payload = _resolve_token(token, db)
        if payload:
            user = CurrentUser(id=UUID(payload["sub"]), org_id=UUID(
                payload["org_id"]), role=payload.get("role", "admin"), name=payload.get("name", ""))
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    feature = _verify_feature_access(db, feature_id, user)
    spec_c, plan_c, tests_c, scaffold_c, kb_entries, trace = _load_export_data(
        db, feature)

    from app.services.export_service import export_feature_xlsx
    xlsx_bytes = export_feature_xlsx(
        feature, spec_c, plan_c, tests_c, scaffold_c, kb_entries, trace)

    fname = (spec_c.get("feature_name") or feature.description or "feature")[
        :40].replace(" ", "_")
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}.xlsx"},
    )


@router.get("/features/{feature_id}/export/markdown")
def export_markdown(
    feature_id: UUID,
    token: str = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_optional_user),
):
    """Export feature as full markdown document."""
    if not user and token:
        from app.deps import _resolve_token
        payload = _resolve_token(token, db)
        if payload:
            user = CurrentUser(id=UUID(payload["sub"]), org_id=UUID(
                payload["org_id"]), role=payload.get("role", "admin"), name=payload.get("name", ""))
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    feature = _verify_feature_access(db, feature_id, user)
    spec_c, plan_c, tests_c, scaffold_c, kb_entries, trace = _load_export_data(
        db, feature)

    from app.services.export_service import export_feature_markdown
    md = export_feature_markdown(
        feature, spec_c, plan_c, tests_c, scaffold_c, kb_entries, trace)

    fname = (spec_c.get("feature_name") or feature.description or "feature")[
        :40].replace(" ", "_")
    return StreamingResponse(
        iter([md]),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={fname}.md"},
    )
