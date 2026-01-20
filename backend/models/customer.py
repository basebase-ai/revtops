"""
Customer model representing organizations using the platform.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.user import User


class Customer(Base):
    """Customer model representing an organization/company using the platform."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    salesforce_instance_url: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    salesforce_org_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    system_oauth_token_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    system_oauth_refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    token_owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    users: Mapped[list["User"]] = relationship(
        "User", back_populates="customer", foreign_keys="User.customer_id"
    )

    def to_dict(self) -> dict[str, Optional[str]]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "salesforce_org_id": self.salesforce_org_id,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
        }
