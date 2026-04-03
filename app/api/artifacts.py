from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.artifact import Artifact
from app.schemas.artifact import ArtifactResponse
from app.deps import get_current_user, CurrentUser

router = APIRouter()


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    # Org isolation: verify artifact's project belongs to user's org
    if artifact.project_id:
        from app.models.project import Project
        project = db.get(Project, artifact.project_id)
        if project and project.org_id and project.org_id != user.org_id:
            raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get("/artifacts/{artifact_id}/trace")
def get_trace(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Walk the parent chain and find children to build the traceability graph."""
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Org isolation
    if artifact.project_id:
        from app.models.project import Project
        project = db.get(Project, artifact.project_id)
        if project and project.org_id and project.org_id != user.org_id:
            raise HTTPException(status_code=404, detail="Artifact not found")

    # Walk up the parent chain (with depth limit to prevent infinite loops)
    chain = []
    current = artifact
    max_depth = 50
    seen = set()
    while current and max_depth > 0:
        if current.id in seen:
            break  # Cycle detected
        seen.add(current.id)
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
        max_depth -= 1

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


@router.get("/artifacts/{artifact_id}/diff")
def get_artifact_diff(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get diff between current artifact and its previous version."""
    current = db.get(Artifact, artifact_id)
    if not current:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Org isolation
    if current.project_id:
        from app.models.project import Project
        project = db.get(Project, current.project_id)
        if project and project.org_id and project.org_id != user.org_id:
            raise HTTPException(status_code=404, detail="Artifact not found")

    if not current.previous_version_id:
        return {"has_diff": False}

    previous = db.get(Artifact, current.previous_version_id)
    if not previous:
        return {"has_diff": False}

    return {
        "has_diff": True,
        "current": {
            "id": current.id,
            "version": current.version,
            "content": current.content,
            "name": current.name,
        },
        "previous": {
            "id": previous.id,
            "version": previous.version,
            "content": previous.content,
            "name": previous.name,
        },
    }
