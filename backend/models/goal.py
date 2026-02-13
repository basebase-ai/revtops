"""
Goal model - normalized representation of revenue goals and quotas.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.pipeline import Pipeline
    from models.user import User


class Goal(Base):
    """Goal model representing revenue goals, quotas, and targets from CRM."""

    __tablename__ = "goals"
    __table_args__ = (
        Index("idx_goals_organization", "organization_id"),
        Index("idx_goals_owner", "owner_id"),
        Index(
            "uq_goals_org_source",
            "organization_id",
            "source_system",
            "source_id",
            unique=True,
            postgresql_where=text("source_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(
        String(50), default="hubspot", nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", onupdate="CASCADE"),
        nullable=True,
    )
    pipeline_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id"), nullable=True
    )
    goal_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'revenue', 'deals_closed', 'calls', etc.

    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="synced"
    )

    # Relationships
    owner: Mapped[Optional["User"]] = relationship("User", foreign_keys=[owner_id])
    pipeline: Mapped[Optional["Pipeline"]] = relationship("Pipeline")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "target_amount": float(self.target_amount) if self.target_amount else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "owner_id": str(self.owner_id) if self.owner_id else None,
            "pipeline_id": str(self.pipeline_id) if self.pipeline_id else None,
            "goal_type": self.goal_type,
            "source_id": self.source_id,
            "source_system": self.source_system,
            "sync_status": self.sync_status,
        }
