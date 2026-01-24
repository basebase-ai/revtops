"""
Organization model representing companies using the Revtops platform.
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


class Organization(Base):
    """Organization model representing a company/tenant using Revtops."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email_domain: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )  # e.g., "acmecorp.com" - used to auto-match new users
    logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    # Legacy Salesforce fields (kept for backwards compatibility)
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
        "User", back_populates="organization", foreign_keys="User.organization_id"
    )
    token_owner: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[token_owner_user_id]
    )

    def to_dict(self) -> dict[str, Optional[str]]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "email_domain": self.email_domain,
            "salesforce_org_id": self.salesforce_org_id,
            "last_sync_at": f"{self.last_sync_at.isoformat()}Z" if self.last_sync_at else None,
        }
