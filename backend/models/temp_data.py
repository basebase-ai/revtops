"""
TempData model – flexible JSONB storage for agent-computed results.

Agents and workflows write interim / computed outputs here (deal confidence
scores, churn risk, engagement grades, etc.).  Rows are optionally linked
to existing entities via soft entity_type / entity_id references.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class TempData(Base):
    """Flexible key/value store for agent-generated results."""

    __tablename__ = "temp_data"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Soft entity reference (no FK constraint – can point to any table)
    entity_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Namespace + key for structured lookups
    namespace: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Payload
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Provenance
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    # Optional TTL
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        result: dict[str, Any] = {
            "id": str(self.id),
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "namespace": self.namespace,
            "key": self.key,
            "value": self.value,
            "metadata": self.metadata,
            "created_by_user_id": str(self.created_by_user_id) if self.created_by_user_id else None,
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
            "expires_at": f"{self.expires_at.isoformat()}Z" if self.expires_at else None,
        }
        return result
