from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(
        String(20), default="gathering"
    )  # gathering, spec_review, plan_review, qa_review, done
    spec_artifact_id: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    plan_artifact_id: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    tests_artifact_id: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
