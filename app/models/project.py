from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    github_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, analyzing, ready, failed
    s3_repo_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # S3 key for cloned repo archive
    codebase_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
