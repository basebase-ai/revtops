"""
Artifact model - saved analyses, dashboards, and reports.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class Artifact(Base):
    """Artifact model for saved analyses, dashboards, and reports."""

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )

    type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'dashboard', 'report', 'analysis'
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # Dashboard structure/queries
    snapshot_data: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # Data at creation time
    is_live: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )  # Refresh on load vs static

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    last_viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "config": self.config,
            "snapshot_data": self.snapshot_data,
            "is_live": self.is_live,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "user_id": str(self.user_id),
        }
