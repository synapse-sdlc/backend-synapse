from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class RepositoryCreate(BaseModel):
    name: str
    github_url: str
    github_token: Optional[str] = None
    repo_type: Optional[str] = None  # frontend, backend, mobile, infra, shared, other
    config: Optional[dict] = None


class RepositoryResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    repo_type: Optional[str]
    github_url: str
    analysis_status: str
    config: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RepositoryUpdate(BaseModel):
    name: Optional[str] = None
    repo_type: Optional[str] = None
    config: Optional[dict] = None
