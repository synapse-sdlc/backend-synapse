import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # spec, plan, architecture, tests, kb
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(12), ForeignKey("artifacts.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft, approved, superseded
    version: Mapped[int] = mapped_column(Integer, default=1)
    feature_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("features.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
