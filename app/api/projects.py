from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.schemas.project import ProjectCreate, ProjectResponse
from app.deps import get_current_user, get_optional_user, CurrentUser

router = APIRouter()


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = Project(name=body.name, github_url=body.github_url, org_id=user.org_id)

    # Encrypt and store GitHub token if provided
    if body.github_token:
        from app.utils.crypto import encrypt_token
        project.github_token_encrypted = encrypt_token(body.github_token)

    db.add(project)
    db.commit()
    db.refresh(project)

    # Trigger codebase analysis if GitHub URL provided
    if body.github_url:
        from app.workers.tasks import analyze_codebase_task
        analyze_codebase_task.delay(str(project.id), body.github_url)

    return project


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    # Scoped to user's org
    result = db.execute(
        select(Project).where(Project.org_id == user.org_id).order_by(Project.created_at.desc())
    )
    return result.scalars().all()


@router.post("/projects/{project_id}/reanalyze", response_model=ProjectResponse)
def reanalyze_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.github_url:
        raise HTTPException(status_code=400, detail="No GitHub URL to analyze")

    project.analysis_status = "pending"
    db.commit()
    db.refresh(project)

    from app.workers.tasks import analyze_codebase_task
    analyze_codebase_task.delay(str(project.id), project.github_url)

    return project


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # Verify org ownership
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
