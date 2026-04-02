from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.feature import Feature
from app.models.artifact import Artifact
from app.schemas.feature import FeatureCreate, FeatureResponse, MessageRequest

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
