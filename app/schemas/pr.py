from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class LinkPRRequest(BaseModel):
    pr_url: str  # e.g. "https://github.com/org/repo/pull/42"


class PullRequestLinkResponse(BaseModel):
    id: UUID
    feature_id: UUID
    repo_full_name: str
    pr_number: int
    pr_url: str
    title: str
    state: str
    merged_at: Optional[datetime] = None
    diff_summary: Optional[str] = None
    files_changed: Optional[list] = None
    commit_messages: Optional[list] = None
    kb_updated: bool
    deployment_status: Optional[dict] = None
    created_at: datetime
    synced_at: Optional[datetime] = None

    class Config:
        from_attributes = True
