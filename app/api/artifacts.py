from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.artifact import Artifact
from app.schemas.artifact import ArtifactResponse

router = APIRouter()


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get("/artifacts/{artifact_id}/trace")
def get_trace(artifact_id: str, db: Session = Depends(get_db)):
    """Walk the parent chain and find children to build the traceability graph."""
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Walk up the parent chain
    chain = []
    current = artifact
    while current:
        chain.insert(0, {
            "id": current.id,
            "type": current.type,
            "name": current.name,
            "status": current.status,
            "version": current.version,
            "parent_id": current.parent_id,
        })
        if current.parent_id:
            current = db.get(Artifact, current.parent_id)
        else:
            current = None

    # Find children of the original artifact
    result = db.execute(
        select(Artifact).where(Artifact.parent_id == artifact_id)
    )
    children = [
        {
            "id": a.id,
            "type": a.type,
            "name": a.name,
            "status": a.status,
            "version": a.version,
            "parent_id": a.parent_id,
        }
        for a in result.scalars().all()
    ]

    return {"chain": chain, "children": children}
