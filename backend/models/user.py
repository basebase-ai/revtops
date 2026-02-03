"""
User model for authentication and permissions.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.organization import Organization
    from models.deal import Deal
    from models.sheet_import import SheetImport
    from models.user_tool_setting import UserToolSetting


class User(Base):
    """User model representing authenticated users."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    salesforce_user_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    role: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'ae', 'sales_manager', 'cro', 'admin'
    roles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )  # Global roles like ['global_admin']
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
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
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization", back_populates="users", foreign_keys=[organization_id]
    )
    deals: Mapped[list["Deal"]] = relationship("Deal", back_populates="owner")
    sheet_imports: Mapped[list["SheetImport"]] = relationship(
        "SheetImport", back_populates="user"
    )
    tool_settings: Mapped[list["UserToolSetting"]] = relationship(
        "UserToolSetting", back_populates="user"
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
            "organization_id": str(self.organization_id) if self.organization_id else None,
        }
