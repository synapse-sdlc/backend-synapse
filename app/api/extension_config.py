from uuid import UUID
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.project import Project
from app.models.extension_config import ExtensionConfig
from app.deps import get_current_user, CurrentUser
from app.utils.crypto import encrypt_token, decrypt_token
from pydantic import BaseModel

router = APIRouter()


class ExtensionTokenSave(BaseModel):
    token: str


class ExtensionConfigResponse(BaseModel):
    project_id: UUID
    configured: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _verify_project(db: Session, project_id: UUID, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project or (project.org_id and project.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get(
    "/projects/{project_id}/extension-config",
    response_model=ExtensionConfigResponse,
    tags=["extension-config"],
)
def get_extension_config(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    cfg = db.execute(
        select(ExtensionConfig).where(ExtensionConfig.project_id == project_id)
    ).scalars().first()
    if not cfg:
        return ExtensionConfigResponse(project_id=project_id, configured=False)
    return ExtensionConfigResponse(
        project_id=project_id,
        configured=True,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


@router.post(
    "/projects/{project_id}/extension-config",
    response_model=ExtensionConfigResponse,
    status_code=201,
    tags=["extension-config"],
)
def save_extension_config(
    project_id: UUID,
    body: ExtensionTokenSave,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)

    if not body.token or not body.token.strip():
        raise HTTPException(status_code=422, detail="Token must not be empty")

    encrypted = encrypt_token(body.token.strip())
    now = datetime.utcnow()

    existing = db.execute(
        select(ExtensionConfig).where(ExtensionConfig.project_id == project_id)
    ).scalars().first()

    if existing:
        existing.token_encrypted = encrypted
        existing.updated_at = now
    else:
        existing = ExtensionConfig(
            project_id=project_id,
            token_encrypted=encrypted,
            updated_at=now,
        )
        db.add(existing)

    db.commit()
    db.refresh(existing)
    return ExtensionConfigResponse(
        project_id=project_id,
        configured=True,
        created_at=existing.created_at,
        updated_at=existing.updated_at,
    )


@router.delete(
    "/projects/{project_id}/extension-config",
    status_code=204,
    tags=["extension-config"],
)
def delete_extension_config(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _verify_project(db, project_id, user)
    cfg = db.execute(
        select(ExtensionConfig).where(ExtensionConfig.project_id == project_id)
    ).scalars().first()
    if cfg:
        db.delete(cfg)
        db.commit()
