from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class GithubConfig(Base):
    __tablename__ = "github_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), unique=True, nullable=False
    )
    # Personal Access Token for GitHub API calls (encrypted at rest)
    github_token_encrypted: Mapped[Optional[str]
                                   ] = mapped_column(Text, nullable=True)
    # URL routing token — embedded in the per-project webhook URL path
    webhook_secret: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True)
    # HMAC signing secret — user pastes this into GitHub webhook settings
    signing_secret: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow)
