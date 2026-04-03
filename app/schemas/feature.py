from typing import Optional, Any

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class FeatureCreate(BaseModel):
    description: str


class FeatureResponse(BaseModel):
    id: UUID
    project_id: UUID
    description: str
    phase: str
    spec_artifact_id: Optional[str]
    plan_artifact_id: Optional[str]
    tests_artifact_id: Optional[str]
    jira_epic_key: Optional[str] = None
    agent_task_id: Optional[str] = None
    total_turns: int = 0
    total_duration_ms: int = 0
    estimated_hours_saved: float = 0.0
    created_at: datetime

    class Config:
        from_attributes = True


class MessageRequest(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: UUID
    feature_id: UUID
    role: str
    content: str
    tool_name: Optional[str]
    user_id: Optional[UUID] = None
    user_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RejectRequest(BaseModel):
    reason: str
