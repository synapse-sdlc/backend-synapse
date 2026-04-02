
from datetime import datetime
from uuid import UUID
from typing import Any, Optional
from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    id: str
    type: str
    name: str
    content: Any
    content_md: Optional[str]
    parent_id: Optional[str]
    status: str
    version: int
    feature_id: Optional[UUID]
    project_id: Optional[UUID]
    created_at: datetime

    class Config:
        from_attributes = True
