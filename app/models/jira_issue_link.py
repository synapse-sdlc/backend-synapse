from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class JiraIssueLink(Base):
    __tablename__ = "jira_issue_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("features.id"), nullable=False)
    issue_key: Mapped[str] = mapped_column(String(30), nullable=False)  # e.g. "SYN-42"
    issue_type: Mapped[str] = mapped_column(String(20), nullable=False)  # epic, story, subtask, test
    issue_url: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="To Do")
    parent_issue_key: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    source_artifact_id: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    source_item_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "US-1", "ST-3"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
