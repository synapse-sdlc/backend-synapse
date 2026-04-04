from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db import Base


class PullRequestLink(Base):
    __tablename__ = "pull_request_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("features.id"), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(
        String(255), nullable=False)  # "org/repo"
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), default="open")  # open, closed, merged
    merged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True)
    diff_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    files_changed: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    commit_messages: Mapped[Optional[dict]
                            ] = mapped_column(JSONB, nullable=True)
    kb_updated: Mapped[bool] = mapped_column(Boolean, default=False)
    deployment_status: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True)  # {branch, run_url, conclusion, completed_at}
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow)
    synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True)
