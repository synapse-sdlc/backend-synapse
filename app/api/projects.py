import json as json_mod
import hashlib
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.models.repository import Repository
from app.models.artifact import Artifact
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.schemas.repository import RepositoryResponse
from app.deps import get_current_user, CurrentUser

router = APIRouter()


def _project_to_response(project: Project, db: Session) -> dict:
    """Build ProjectResponse with repositories eagerly loaded."""
    repos = db.execute(
        select(Repository)
        .where(Repository.project_id == project.id)
        .order_by(Repository.created_at)
    ).scalars().all()

    # Derive analysis_status from repos if any exist
    if repos:
        statuses = [r.analysis_status for r in repos]
        if all(s == "ready" for s in statuses):
            derived_status = "ready"
        elif any(s == "failed" for s in statuses):
            derived_status = "failed"
        elif any(s == "analyzing" for s in statuses):
            derived_status = "analyzing"
        else:
            derived_status = "pending"
    else:
        derived_status = project.analysis_status

    return {
        "id": project.id,
        "name": project.name,
        "github_url": project.github_url,
        "analysis_status": derived_status,
        "config": project.config,
        "repositories": [RepositoryResponse.model_validate(r) for r in repos],
        "created_at": project.created_at,
    }


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = Project(
        name=body.name,
        org_id=user.org_id,
        config=body.config,
    )

    # Backward compatibility: if single github_url provided (old format)
    if body.github_url and not body.repositories:
        project.github_url = body.github_url
        if body.github_token:
            from app.utils.crypto import encrypt_token
            project.github_token_encrypted = encrypt_token(body.github_token)

    db.add(project)
    db.commit()
    db.refresh(project)

    # Handle repositories list (new multi-repo format)
    if body.repositories:
        for repo_data in body.repositories:
            repo = Repository(
                project_id=project.id,
                name=repo_data.name,
                repo_type=repo_data.repo_type,
                github_url=repo_data.github_url,
                config=repo_data.config,
            )
            if repo_data.github_token:
                from app.utils.crypto import encrypt_token
                repo.github_token_encrypted = encrypt_token(repo_data.github_token)
            db.add(repo)
        db.commit()

        # Trigger analysis for each repo
        from app.workers.tasks import analyze_repository_task
        repos = db.execute(
            select(Repository).where(Repository.project_id == project.id)
        ).scalars().all()
        for repo in repos:
            analyze_repository_task.delay(str(repo.id))

    elif body.github_url:
        # Legacy single-repo: create a Repository row automatically
        repo = Repository(
            project_id=project.id,
            name="main",
            github_url=body.github_url,
        )
        if body.github_token:
            from app.utils.crypto import encrypt_token
            repo.github_token_encrypted = encrypt_token(body.github_token)
        db.add(repo)
        db.commit()

        from app.workers.tasks import analyze_repository_task
        db.refresh(repo)
        analyze_repository_task.delay(str(repo.id))

    return _project_to_response(project, db)


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    result = db.execute(
        select(Project).where(Project.org_id == user.org_id).order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    if not projects:
        return []

    # Batch-load all repositories for all projects in ONE query (avoids N+1)
    project_ids = [p.id for p in projects]
    all_repos = db.execute(
        select(Repository).where(Repository.project_id.in_(project_ids)).order_by(Repository.created_at)
    ).scalars().all()
    repo_map = {}
    for r in all_repos:
        repo_map.setdefault(r.project_id, []).append(r)

    responses = []
    for p in projects:
        repos = repo_map.get(p.id, [])
        if repos:
            statuses = [r.analysis_status for r in repos]
            if all(s == "ready" for s in statuses):
                derived_status = "ready"
            elif any(s == "failed" for s in statuses):
                derived_status = "failed"
            elif any(s == "analyzing" for s in statuses):
                derived_status = "analyzing"
            else:
                derived_status = "pending"
        else:
            derived_status = p.analysis_status

        responses.append({
            "id": p.id,
            "name": p.name,
            "github_url": p.github_url,
            "analysis_status": derived_status,
            "config": p.config,
            "repositories": [RepositoryResponse.model_validate(r) for r in repos],
            "created_at": p.created_at,
        })
    return responses


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_to_response(project, db)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.name is not None:
        project.name = body.name
    if body.config is not None:
        project.config = body.config

    db.commit()
    db.refresh(project)
    return _project_to_response(project, db)


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

    # Reanalyze all repositories
    repos = db.execute(
        select(Repository).where(Repository.project_id == project_id)
    ).scalars().all()

    if repos:
        from app.workers.tasks import analyze_repository_task
        for repo in repos:
            repo.analysis_status = "pending"
            analyze_repository_task.delay(str(repo.id))
        db.commit()
    elif project.github_url:
        # Legacy fallback
        project.analysis_status = "pending"
        db.commit()
        from app.workers.tasks import analyze_codebase_task
        analyze_codebase_task.delay(str(project.id), project.github_url)

    db.refresh(project)
    return _project_to_response(project, db)


@router.get("/projects/{project_id}/architecture")
def get_project_architecture(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.org_id and project.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.models.artifact import Artifact

    # Return all architecture artifacts for this project (one per repo + optional unified)
    result = db.execute(
        select(Artifact)
        .where(Artifact.project_id == str(project_id), Artifact.type.in_(["architecture", "project_architecture"]))
        .order_by(Artifact.created_at.desc())
    )
    artifacts = result.scalars().all()

    if not artifacts:
        raise HTTPException(status_code=404, detail="No architecture artifact found. Run codebase analysis first.")

    # If single artifact, return it directly (backward compatible)
    if len(artifacts) == 1:
        return artifacts[0]

    # Multiple: return list
    return artifacts


@router.post("/projects/{project_id}/architecture/upload")
async def upload_architecture(
    project_id: UUID,
    file: UploadFile = File(None),
    content: str = Form(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Upload an existing architecture document to seed codebase analysis.

    Accepts a file (.json, .md, .txt) or raw content via form field.
    """
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")

    raw_content = None
    if file:
        raw_content = (await file.read()).decode("utf-8")
    elif content:
        raw_content = content
    else:
        raise HTTPException(status_code=400, detail="Provide a file or content")

    # Try to parse as JSON, otherwise store as raw text
    try:
        parsed = json_mod.loads(raw_content)
    except (json_mod.JSONDecodeError, TypeError):
        parsed = {"raw_text": raw_content, "source": "uploaded"}

    # Generate artifact ID
    art_id = hashlib.md5(f"architecture:uploaded:{time.time()}".encode()).hexdigest()[:12]

    artifact = Artifact(
        id=art_id,
        type="architecture",
        name="Uploaded Architecture",
        content=parsed,
        status="approved",
        version=1,
        project_id=str(project_id),
    )
    db.merge(artifact)

    project.uploaded_architecture_id = art_id
    db.commit()

    return {"artifact_id": art_id, "status": "uploaded"}
