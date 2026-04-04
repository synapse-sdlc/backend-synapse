from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class GithubConfigSave(BaseModel):
    github_token: Optional[str] = None


class GithubConfigResponse(BaseModel):
    id: UUID
    project_id: UUID
    # Never expose the raw token; just signal whether one is stored
    has_token: bool = False
    webhook_secret: Optional[str] = None
    signing_secret: Optional[str] = None
    # Computed and injected by the endpoint handler
    webhook_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
