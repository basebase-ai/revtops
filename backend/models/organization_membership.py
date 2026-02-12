"""
Organization membership model for multi-org support.

Tracks which organizations a user belongs to and their role in each.
User.organization_id remains the "active org" for the current session.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.user import User
    from models.organization import Organization


class OrganizationMembership(Base):
    """Tracks a user's membership in an organization."""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="member"
    )  # 'admin', 'member', 'ae', 'sales_manager', 'cro'
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # 'invited', 'active', 'deactivated'
    invited_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], backref="memberships"
    )
    organization: Mapped["Organization"] = relationship(
        "Organization", foreign_keys=[organization_id]
    )
    invited_by: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[invited_by_user_id]
    )
