from typing import Optional, Any

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str
    github_url: Optional[str] = None


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    github_url: Optional[str]
    analysis_status: str
    created_at: datetime

    class Config:
        from_attributes = True
