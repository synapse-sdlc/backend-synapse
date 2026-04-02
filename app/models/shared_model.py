from typing import Optional
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db import Base


class SharedModel(Base):
    __tablename__ = "shared_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # "User", "Order", "Product"
    canonical_repo_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    schema_: Mapped[Optional[dict]] = mapped_column("schema", JSONB, nullable=True)  # {fields, relationships}
    usages: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # [{repo_id, file, type}]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
