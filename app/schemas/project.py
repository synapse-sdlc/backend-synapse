from typing import Optional, Any

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel

from app.schemas.repository import RepositoryCreate, RepositoryResponse


class ProjectCreate(BaseModel):
    name: str
    # Legacy single-repo fields (still accepted for backward compatibility)
    github_url: Optional[str] = None
    github_token: Optional[str] = None
    # New multi-repo field
    repositories: Optional[list[RepositoryCreate]] = None
    config: Optional[dict] = None


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    github_url: Optional[str] = None
    analysis_status: str
    config: Optional[dict] = None
    repositories: list[RepositoryResponse] = []
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
