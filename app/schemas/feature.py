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
    spec_artifact_id: str | None
    plan_artifact_id: str | None
    tests_artifact_id: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class MessageRequest(BaseModel):
    content: str
