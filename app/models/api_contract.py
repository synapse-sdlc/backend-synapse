from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db import Base


class ApiContract(Base):
    __tablename__ = "api_contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)  # GET, POST, PUT, DELETE
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    provider_repo_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    response_schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    auth_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    consumers: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # [{repo_id, file, line}]
    extracted_from: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # analysis, manual, spec
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
