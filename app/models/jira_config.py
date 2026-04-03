from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class JiraConfig(Base):
    __tablename__ = "jira_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), unique=True, nullable=False)
    site_url: Mapped[str] = mapped_column(Text, nullable=False)  # https://myorg.atlassian.net
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet-encrypted
    default_project_key: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "SYN"
    webhook_secret: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # Our secret in URL path
    jira_webhook_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Jira's secret for HMAC verification
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
