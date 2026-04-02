from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # "frontend", "backend", "mobile"
    repo_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # frontend, backend, mobile, infra, shared, other
    github_url: Mapped[str] = mapped_column(Text, nullable=False)
    github_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_repo_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, analyzing, ready, failed
    codebase_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
