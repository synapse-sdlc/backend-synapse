from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class KnowledgeEntryResponse(BaseModel):
    id: UUID
    project_id: UUID
    feature_id: Optional[UUID] = None
    entry_type: str
    title: str
    content: str
    metadata_: Optional[dict] = None
    tags: Optional[list[str]] = None
    source_artifact_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class KnowledgeQueryRequest(BaseModel):
    question: str
    persona: str = "developer"  # po, qa, developer, tech_lead


class KnowledgeQueryResponse(BaseModel):
    answer: str
    confidence: str = "medium"  # high, medium, low
    sources: list[dict] = []
    follow_up_questions: list[str] = []


class ApiContractResponse(BaseModel):
    id: UUID
    project_id: UUID
    method: str
    path: str
    provider_repo_id: Optional[UUID] = None
    description: Optional[str] = None
    request_schema: Optional[dict] = None
    response_schema: Optional[dict] = None
    auth_required: Optional[bool] = None
    consumers: Optional[list] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SharedModelResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    canonical_repo_id: Optional[UUID] = None
    schema_: Optional[dict] = None
    usages: Optional[list] = None
    created_at: datetime

    class Config:
        from_attributes = True
