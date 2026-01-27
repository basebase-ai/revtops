"""
Deal model - normalized representation of opportunities.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import ARRAY, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.account import Account
    from models.activity import Activity
    from models.pipeline import Pipeline
    from models.user import User


class Deal(Base):
    """Deal model representing sales opportunities."""

    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(
        String(50), default="salesforce", nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Standard fields
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    pipeline_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id"), nullable=True
    )
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    stage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    probability: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    close_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_modified_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    # Permission tracking
    visible_to_user_ids: Mapped[Optional[list[uuid.UUID]]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )

    # Flexible fields
    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Metadata
    synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # Relationships
    account: Mapped[Optional["Account"]] = relationship(
        "Account", back_populates="deals"
    )
    owner: Mapped[Optional["User"]] = relationship("User", back_populates="deals")
    pipeline: Mapped[Optional["Pipeline"]] = relationship(
        "Pipeline", back_populates="deals"
    )
    activities: Mapped[list["Activity"]] = relationship(
        "Activity", back_populates="deal"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "amount": float(self.amount) if self.amount else None,
            "stage": self.stage,
            "probability": self.probability,
            "close_date": self.close_date.isoformat() if self.close_date else None,
            "account_id": str(self.account_id) if self.account_id else None,
            "owner_id": str(self.owner_id) if self.owner_id else None,
            "pipeline_id": str(self.pipeline_id) if self.pipeline_id else None,
            "custom_fields": self.custom_fields,
            "source_id": self.source_id,
            "source_system": self.source_system,
        }
