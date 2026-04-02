from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.feature import Feature
from app.models.artifact import Artifact
from app.models.message import Message
from app.schemas.feature import FeatureCreate, FeatureResponse, MessageRequest, MessageResponse

router = APIRouter()


@router.post("/projects/{project_id}/features", response_model=FeatureResponse, status_code=201)
def create_feature(project_id: UUID, body: FeatureCreate, db: Session = Depends(get_db)):
    feature = Feature(project_id=project_id, description=body.description)
    db.add(feature)
    db.commit()
    db.refresh(feature)

    # Enqueue initial agent turn: ask clarifying questions
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
def get_feature(feature_id: UUID, db: Session = Depends(get_db)):
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    return feature


@router.post("/features/{feature_id}/message", status_code=202)
def send_message(feature_id: UUID, body: MessageRequest, db: Session = Depends(get_db)):
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    from app.workers.tasks import agent_run_task
    agent_run_task.delay(str(feature_id), body.content)

    return {"status": "accepted", "feature_id": str(feature_id)}


@router.post("/features/{feature_id}/approve", status_code=200)
def approve_feature(feature_id: UUID, db: Session = Depends(get_db)):
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    phase = feature.phase

    # Update current artifact status to approved
    from app.workers.tasks import approval_agent_task

    if phase == "spec_review" and feature.spec_artifact_id:
        artifact = db.get(Artifact, feature.spec_artifact_id)
        if artifact:
            artifact.status = "approved"
        feature.phase = "plan_review"
        db.commit()
        approval_agent_task.delay(str(feature_id))

    elif phase == "plan_review" and feature.plan_artifact_id:
        artifact = db.get(Artifact, feature.plan_artifact_id)
        if artifact:
            artifact.status = "approved"
        feature.phase = "qa_review"
        db.commit()
        approval_agent_task.delay(str(feature_id))

    elif phase == "qa_review" and feature.tests_artifact_id:
        artifact = db.get(Artifact, feature.tests_artifact_id)
        if artifact:
            artifact.status = "approved"
        feature.phase = "done"

    else:
        raise HTTPException(status_code=400, detail=f"Cannot approve in phase: {phase}")

    db.commit()
    db.refresh(feature)

    return {"status": "approved", "phase": feature.phase, "feature_id": str(feature_id)}


@router.get("/projects/{project_id}/features", response_model=list[FeatureResponse])
def list_features(project_id: UUID, db: Session = Depends(get_db)):
    result = db.execute(
        select(Feature).where(Feature.project_id == project_id).order_by(Feature.created_at.desc())
    )
    return result.scalars().all()


@router.get("/features/{feature_id}/messages", response_model=list[MessageResponse])
def list_messages(feature_id: UUID, db: Session = Depends(get_db)):
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    result = db.execute(
        select(Message)
        .where(Message.feature_id == feature_id)
        .where(Message.role.in_(["user", "assistant"]))  # Skip tool messages for chat display
        .order_by(Message.created_at)
    )
    return result.scalars().all()


@router.get("/features/{feature_id}/jira-preview")
def jira_preview(feature_id: UUID, db: Session = Depends(get_db)):
    """Build Jira ticket preview from spec + plan + tests artifacts."""
    import json

    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

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

    # Build preview
    feature_name = spec_data.get("feature_name") or plan_data.get("feature_name", feature.description)
    user_stories = spec_data.get("user_stories", [])
    subtasks = plan_data.get("subtasks", [])
    test_suites = tests_data.get("test_suites", [])

    total_hours = sum(t.get("estimated_hours", 0) for t in subtasks if isinstance(t, dict))
    total_tests = sum(len(s.get("test_cases", [])) for s in test_suites if isinstance(s, dict))

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
