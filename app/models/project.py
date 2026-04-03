from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Legacy single-repo fields (kept for backward compatibility, new repos use Repository model)
    github_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, analyzing, ready, failed
    github_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_repo_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    codebase_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # New multi-repo fields
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    uploaded_architecture_id: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    custom_skills: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
