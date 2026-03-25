"""
User model for authentication and permissions.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.organization import Organization
    from models.deal import Deal
    from models.user_tool_setting import UserToolSetting
    from models.change_session import ChangeSession
    from models.credit_transaction import CreditTransaction


class User(Base):
    """User model representing authenticated users."""

    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_one_guest_per_org",
            "guest_organization_id",
            unique=True,
            postgresql_where=text(
                "is_guest = true AND guest_organization_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    guest_organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    role: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'ae', 'sales_manager', 'cro', 'admin'
    roles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )  # Global roles like ['global_admin']
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True, unique=True
    )  # E.164 format, e.g. "+14155551234"
    phone_number_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )  # When set, outbound SMS to this number is allowed
    sms_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whatsapp_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_guest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Waitlist fields
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # 'waitlist', 'invited', 'active'
    waitlist_data: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # {title, company_name, num_employees, apps_of_interest[], core_needs[]}
    waitlisted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    guest_organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="guest_users",
        foreign_keys=[guest_organization_id],
    )
    deals: Mapped[list["Deal"]] = relationship(
        "Deal", back_populates="owner", foreign_keys="Deal.owner_id"
    )
    tool_settings: Mapped[list["UserToolSetting"]] = relationship(
        "UserToolSetting", back_populates="user"
    )
    change_sessions: Mapped[list["ChangeSession"]] = relationship(
        "ChangeSession", back_populates="user", foreign_keys="ChangeSession.user_id"
    )
    credit_transactions: Mapped[list["CreditTransaction"]] = relationship(
        "CreditTransaction", back_populates="user"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "roles": self.roles,
            "status": self.status,
            "avatar_url": self.avatar_url,
            "phone_number": self.phone_number,
            "phone_number_verified_at": self.phone_number_verified_at.isoformat() if self.phone_number_verified_at else None,
            "sms_consent": self.sms_consent,
            "whatsapp_consent": self.whatsapp_consent,
            "is_guest": self.is_guest,
            "organization_id": (
                str(self.guest_organization_id)
                if self.guest_organization_id
                else None
            ),
        }
