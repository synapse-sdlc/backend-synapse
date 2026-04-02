from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.repository import Repository
from app.models.project import Project
from app.schemas.repository import RepositoryCreate, RepositoryResponse, RepositoryUpdate
from app.deps import get_current_user, CurrentUser

router = APIRouter()


def _verify_project_access(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/projects/{project_id}/repositories", response_model=RepositoryResponse, status_code=201)
def add_repository(
    project_id: UUID,
    body: RepositoryCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = _verify_project_access(db, project_id, user)

    repo = Repository(
        project_id=project.id,
        name=body.name,
        repo_type=body.repo_type,
        github_url=body.github_url,
        config=body.config,
    )

    if body.github_token:
        from app.utils.crypto import encrypt_token
        repo.github_token_encrypted = encrypt_token(body.github_token)

    db.add(repo)
    db.commit()
    db.refresh(repo)

    # Trigger analysis
    from app.workers.tasks import analyze_repository_task
    analyze_repository_task.delay(str(repo.id))

    return repo


@router.get("/projects/{project_id}/repositories", response_model=list[RepositoryResponse])
def list_repositories(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project_access(db, project_id, user)
    result = db.execute(
        select(Repository)
        .where(Repository.project_id == project_id)
        .order_by(Repository.created_at)
    )
    return result.scalars().all()


@router.get("/repositories/{repo_id}", response_model=RepositoryResponse)
def get_repository(
    repo_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    _verify_project_access(db, repo.project_id, user)
    return repo


@router.patch("/repositories/{repo_id}", response_model=RepositoryResponse)
def update_repository(
    repo_id: UUID,
    body: RepositoryUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    _verify_project_access(db, repo.project_id, user)

    if body.name is not None:
        repo.name = body.name
    if body.repo_type is not None:
        repo.repo_type = body.repo_type
    if body.config is not None:
        repo.config = body.config

    db.commit()
    db.refresh(repo)
    return repo


@router.delete("/repositories/{repo_id}", status_code=204)
def delete_repository(
    repo_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    _verify_project_access(db, repo.project_id, user)

    db.delete(repo)
    db.commit()


@router.post("/repositories/{repo_id}/reanalyze", response_model=RepositoryResponse)
def reanalyze_repository(
    repo_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    _verify_project_access(db, repo.project_id, user)

    repo.analysis_status = "pending"
    db.commit()
    db.refresh(repo)

    from app.workers.tasks import analyze_repository_task
    analyze_repository_task.delay(str(repo.id))

    return repo
