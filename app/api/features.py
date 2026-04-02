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
            select(JiraConfig).where(JiraConfig.project_id == feature.project_id)
        ).scalars().first()
        if not jira_config:
            return
        from app.workers.tasks import jira_export_task
        jira_export_task.delay(str(feature.id), None)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Auto Jira export failed to queue: {e}")


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
    agent_run_task.delay(str(feature.id), initial_message)

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

    db_msg = Message(
        feature_id=feature_id,
        role="user",
        content=body.content,
        user_id=user.id,
        user_name=user.name or "User",
    )
    db.add(db_msg)
    db.commit()

    from app.workers.tasks import agent_run_task
    agent_run_task.delay(str(feature_id), body.content)

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
        raise HTTPException(status_code=400, detail=f"Cannot approve in phase: {phase}")

    artifact_type, artifact_id = phase_artifact_map[phase]
    if not artifact_id:
        raise HTTPException(status_code=400, detail=f"No {artifact_type} artifact to approve")

    # Atomic phase transition — prevents concurrent duplicate approvals
    next_phase = {"spec_review": "plan_review", "plan_review": "qa_review", "qa_review": "done"}
    result = db.execute(
        update(Feature)
        .where(Feature.id == feature_id, Feature.phase == phase)
        .values(phase=next_phase[phase])
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Feature was already updated by another request")

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
        approval_agent_task.delay(str(feature_id))
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
        raise HTTPException(status_code=400, detail=f"Cannot reject in phase: {phase}")

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
    agent_run_task.delay(str(feature_id), revision_prompt)

    db.refresh(feature)
    return {"status": "rejected", "phase": feature.phase, "feature_id": str(feature_id)}


@router.post("/features/{feature_id}/close", status_code=200)
def close_feature(
    feature_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    feature = _verify_feature_access(db, feature_id, user)

    if feature.phase != "done":
        raise HTTPException(status_code=400, detail="Can only close features in 'done' phase")

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
        logging.getLogger(__name__).warning(f"KB update task failed to queue: {e}")

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
            spec_data = spec_art.content if isinstance(spec_art.content, dict) else {}

    if feature.plan_artifact_id:
        plan_art = db.get(Artifact, feature.plan_artifact_id)
        if plan_art:
            plan_data = plan_art.content if isinstance(plan_art.content, dict) else {}

    if feature.tests_artifact_id:
        tests_art = db.get(Artifact, feature.tests_artifact_id)
        if tests_art:
            tests_data = tests_art.content if isinstance(tests_art.content, dict) else {}

    feature_name = spec_data.get("feature_name") or plan_data.get("feature_name", feature.description)
    user_stories = spec_data.get("user_stories", [])
    subtasks = plan_data.get("subtasks", [])
    test_suites = tests_data.get("test_suites", [])

    def safe_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0

    total_hours = sum(safe_float(t.get("estimated_hours", 0)) for t in subtasks if isinstance(t, dict))
    total_tests = sum(len(s.get("test_cases", s.get("tests", []))) for s in test_suites if isinstance(s, dict))

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
        from app.utils.auth import decode_access_token
        payload = decode_access_token(token)
        if payload:
            from app.deps import CurrentUser as CU
            user = CU(id=UUID(payload["sub"]), org_id=UUID(payload["org_id"]), role=payload.get("role", "admin"), name=payload.get("name", ""))
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    feature = _verify_feature_access(db, feature_id, user)

    if not feature.tests_artifact_id:
        raise HTTPException(status_code=404, detail="No test cases generated yet")

    art = db.get(Artifact, feature.tests_artifact_id)
    if not art:
        raise HTTPException(status_code=404, detail="Tests artifact not found")

    content = art.content if isinstance(art.content, dict) else {}
    test_suites = content.get("test_suites", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Suite", "Type", "Story", "ID", "Title", "Priority", "Preconditions", "Steps", "Expected Result", "Automated"])

    for suite in test_suites:
        if not isinstance(suite, dict):
            continue
        suite_name = suite.get("name", "")
        suite_type = suite.get("type", "")
        story_id = suite.get("story_id", "")

        for tc in suite.get("test_cases", []):
            if not isinstance(tc, dict):
                continue
            preconditions = "; ".join(tc.get("preconditions", [])) if isinstance(tc.get("preconditions"), list) else str(tc.get("preconditions", ""))
            steps = "; ".join(tc.get("steps", [])) if isinstance(tc.get("steps"), list) else str(tc.get("steps", ""))

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
    feature_name = content.get("feature_name", feature.description)[:50].replace(" ", "_")
    filename = f"test_cases_{feature_name}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
