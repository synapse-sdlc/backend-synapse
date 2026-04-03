from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class JiraConfigCreate(BaseModel):
    site_url: str
    user_email: str
    api_token: str
    default_project_key: str


class JiraConfigResponse(BaseModel):
    id: UUID
    project_id: UUID
    site_url: str
    user_email: str
    default_project_key: str
    webhook_secret: Optional[str] = None
    jira_webhook_secret: Optional[str] = None
    webhook_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class JiraExportRequest(BaseModel):
    project_key: Optional[str] = None  # override default


class JiraIssueLinkResponse(BaseModel):
    id: UUID
    feature_id: UUID
    issue_key: str
    issue_type: str
    issue_url: str
    summary: str
    status: str
    parent_issue_key: Optional[str] = None
    source_artifact_id: Optional[str] = None
    source_item_id: Optional[str] = None
    created_at: datetime
    status_synced_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class JiraLinkRequest(BaseModel):
    issue_key: str


class JiraStatusResponse(BaseModel):
    total: int
    done: int
    in_progress: int
    todo: int
