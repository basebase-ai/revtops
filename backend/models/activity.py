"""
Activity model - normalized representation of CRM activities.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.account import Account
    from models.contact import Contact
    from models.deal import Deal
    from models.meeting import Meeting
    from models.user import User


class Activity(Base):
    """Activity model representing CRM activities like calls, emails, meetings."""

    __tablename__ = "activities"
    __table_args__ = (
        Index("idx_activities_organization", "organization_id"),
        Index("idx_activities_deal", "deal_id"),
        Index("idx_activities_date", "activity_date"),
        Index("ix_activities_meeting_id", "meeting_id"),
        Index("ix_activities_source_system", "source_system"),
        Index("ix_activities_org_source_system", "organization_id", "source_system"),
        Index("ix_activities_org_type", "organization_id", "type"),
        Index(
            "uq_activities_org_source",
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
        String(50), default="salesforce", nullable=False
    )
    source_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    deal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=True
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id"), nullable=True
    )
    meeting_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=True
    )

    type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'call', 'email', 'meeting', 'note'
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activity_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=True
    )
    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    
    # Semantic search fields
    searchable_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)
    
    # Change tracking columns (for local modifications)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )

    # Relationships
    deal: Mapped[Optional["Deal"]] = relationship("Deal", back_populates="activities")
    account: Mapped[Optional["Account"]] = relationship("Account")
    contact: Mapped[Optional["Contact"]] = relationship("Contact")
    meeting: Mapped[Optional["Meeting"]] = relationship("Meeting", back_populates="activities")
    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_id])

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "type": self.type,
            "subject": self.subject,
            "description": self.description,
            "activity_date": (
                f"{self.activity_date.isoformat()}Z" if self.activity_date else None
            ),
            "deal_id": str(self.deal_id) if self.deal_id else None,
            "account_id": str(self.account_id) if self.account_id else None,
            "contact_id": str(self.contact_id) if self.contact_id else None,
            "source_id": self.source_id,
            "source_system": self.source_system,
        }
