from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.models.knowledge_entry import KnowledgeEntry
from app.models.api_contract import ApiContract
from app.models.shared_model import SharedModel
from app.models.artifact import Artifact
from app.schemas.knowledge import (
    KnowledgeEntryResponse, KnowledgeQueryRequest, KnowledgeQueryResponse,
    ApiContractResponse, SharedModelResponse,
)
from app.deps import get_current_user, CurrentUser

router = APIRouter()


def _verify_project(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/projects/{project_id}/knowledge/query", status_code=202)
def query_knowledge(
    project_id: UUID,
    body: KnowledgeQueryRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    from app.workers.tasks import knowledge_query_task
    import uuid
    query_id = str(uuid.uuid4())
    knowledge_query_task.delay(str(project_id), body.question, body.persona, query_id)
    return {"status": "accepted", "query_id": query_id}


@router.get("/projects/{project_id}/knowledge/entries", response_model=list[KnowledgeEntryResponse])
def list_knowledge_entries(
    project_id: UUID,
    entry_type: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)

    query = select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id)
    if entry_type:
        query = query.where(KnowledgeEntry.entry_type == entry_type)
    if tag:
        query = query.where(KnowledgeEntry.tags.any(tag))

    query = query.order_by(KnowledgeEntry.created_at.desc()).offset(offset).limit(limit)
    return db.execute(query).scalars().all()


@router.get("/projects/{project_id}/knowledge/entries/{entry_id}", response_model=KnowledgeEntryResponse)
def get_knowledge_entry(
    project_id: UUID,
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    entry = db.get(KnowledgeEntry, entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return entry


@router.get("/projects/{project_id}/knowledge/summary")
def get_knowledge_summary(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)

    # Find accumulated_kb artifact
    artifact = db.execute(
        select(Artifact)
        .where(Artifact.project_id == str(project_id), Artifact.type == "accumulated_kb")
        .order_by(Artifact.created_at.desc())
    ).scalars().first()

    if artifact:
        return artifact.content

    # Fallback: aggregate from knowledge_entries
    entries = db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id)
    ).scalars().all()

    return {
        "total_entries": len(entries),
        "by_type": {
            t: sum(1 for e in entries if e.entry_type == t)
            for t in ("decision", "pattern", "lesson", "architecture_change")
        },
        "recent": [
            {"title": e.title, "type": e.entry_type, "content": e.content[:200]}
            for e in sorted(entries, key=lambda x: x.created_at, reverse=True)[:10]
        ],
    }


# --- API Contracts ---

@router.get("/projects/{project_id}/contracts", response_model=list[ApiContractResponse])
def list_contracts(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    _verify_project(db, project_id, user)
    result = db.execute(
        select(ApiContract)
        .where(ApiContract.project_id == project_id)
        .order_by(ApiContract.method, ApiContract.path)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/projects/{project_id}/shared-models", response_model=list[SharedModelResponse])
def list_shared_models(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    _verify_project(db, project_id, user)
    result = db.execute(
        select(SharedModel)
        .where(SharedModel.project_id == project_id)
        .order_by(SharedModel.name)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()
