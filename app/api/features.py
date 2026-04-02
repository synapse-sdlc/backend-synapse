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

    # TODO: enqueue initial agent turn via Celery
    # The first message should be the feature description + "ask clarifying questions"

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

    # TODO: enqueue agent_run_task(feature_id, body.content) via Celery
    # Return 202 immediately, client listens on SSE for progress

    return {"status": "accepted", "feature_id": str(feature_id)}


@router.post("/features/{feature_id}/approve", status_code=200)
def approve_feature(feature_id: UUID, db: Session = Depends(get_db)):
    feature = db.get(Feature, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    phase = feature.phase

    # Update current artifact status to approved
    if phase == "spec_review" and feature.spec_artifact_id:
        artifact = db.get(Artifact, feature.spec_artifact_id)
        if artifact:
            artifact.status = "approved"
        feature.phase = "plan_review"
        # TODO: enqueue generate_plan task via Celery

    elif phase == "plan_review" and feature.plan_artifact_id:
        artifact = db.get(Artifact, feature.plan_artifact_id)
        if artifact:
            artifact.status = "approved"
        feature.phase = "qa_review"
        # TODO: enqueue generate_tests task via Celery

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
