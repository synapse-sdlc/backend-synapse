from datetime import datetime
from uuid import UUID
from typing import Any
from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    id: str
    type: str
    name: str
    content: Any
    content_md: str | None
    parent_id: str | None
    status: str
    version: int
    feature_id: UUID | None
    project_id: UUID | None
    created_at: datetime

    class Config:
        from_attributes = True
