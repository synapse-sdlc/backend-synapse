import secrets as _secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as _s
from app.db import get_db
from app.deps import get_current_user, CurrentUser
from app.models.github_config import GithubConfig
from app.models.project import Project
from app.schemas.github import GithubConfigResponse, GithubConfigSave
from app.utils.crypto import encrypt_token

router = APIRouter()


def _verify_project(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _build_response(config: GithubConfig) -> GithubConfigResponse:
    return GithubConfigResponse(
        id=config.id,
        project_id=config.project_id,
        has_token=bool(config.github_token_encrypted),
        webhook_secret=config.webhook_secret,
        signing_secret=config.signing_secret,
        webhook_url=(
            f"{_s.public_url.rstrip('/')}/webhooks/github/{config.webhook_secret}"
            if config.webhook_secret
            else None
        ),
        created_at=config.created_at,
    )


@router.get("/projects/{project_id}/github-config", response_model=GithubConfigResponse)
def get_github_config(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    config = db.execute(
        select(GithubConfig).where(GithubConfig.project_id == project_id)
    ).scalars().first()
    if not config:
        raise HTTPException(
            status_code=404, detail="GitHub not configured for this project")
    return _build_response(config)


@router.post("/projects/{project_id}/github-config", response_model=GithubConfigResponse, status_code=201)
def save_github_config(
    project_id: UUID,
    body: GithubConfigSave,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Create or update GitHub configuration for a project.

    On first call the webhook routing secret and signing secret are
    auto-generated.  Subsequent calls update only the GitHub token (pass
    ``null`` / omit to leave the existing token unchanged).
    """
    _verify_project(db, project_id, user)

    existing = db.execute(
        select(GithubConfig).where(GithubConfig.project_id == project_id)
    ).scalars().first()

    if existing:
        # Update token only if a new value was provided
        if body.github_token is not None:
            existing.github_token_encrypted = (
                encrypt_token(body.github_token) if body.github_token else None
            )
        db.commit()
        db.refresh(existing)
        return _build_response(existing)

    config = GithubConfig(
        project_id=project_id,
        github_token_encrypted=(
            encrypt_token(body.github_token) if body.github_token else None
        ),
        webhook_secret=_secrets.token_urlsafe(32),
        signing_secret=_secrets.token_urlsafe(32),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return _build_response(config)


@router.delete("/projects/{project_id}/github-config", status_code=204)
def delete_github_config(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    config = db.execute(
        select(GithubConfig).where(GithubConfig.project_id == project_id)
    ).scalars().first()
    if config:
        db.delete(config)
        db.commit()


@router.post(
    "/projects/{project_id}/github-config/regenerate-url-secret",
    response_model=GithubConfigResponse,
)
def regenerate_webhook_url_secret(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Rotate the URL routing token.  The old webhook URL will stop working."""
    _verify_project(db, project_id, user)
    config = db.execute(
        select(GithubConfig).where(GithubConfig.project_id == project_id)
    ).scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="GitHub not configured")
    config.webhook_secret = _secrets.token_urlsafe(32)
    db.commit()
    db.refresh(config)
    return _build_response(config)


@router.post(
    "/projects/{project_id}/github-config/regenerate-signing-secret",
    response_model=GithubConfigResponse,
)
def regenerate_signing_secret(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Rotate the HMAC signing secret.  GitHub webhook must be updated."""
    _verify_project(db, project_id, user)
    config = db.execute(
        select(GithubConfig).where(GithubConfig.project_id == project_id)
    ).scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="GitHub not configured")
    config.signing_secret = _secrets.token_urlsafe(32)
    db.commit()
    db.refresh(config)
    return _build_response(config)
