"""
User model for authentication and permissions.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.customer import Customer
    from models.deal import Deal


class User(Base):
    """User model representing authenticated users."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True
    )
    salesforce_user_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    role: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'ae', 'sales_manager', 'cro', 'admin'
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    customer: Mapped[Optional["Customer"]] = relationship(
        "Customer", back_populates="users"
    )
    deals: Mapped[list["Deal"]] = relationship("Deal", back_populates="owner")

    def to_dict(self) -> dict[str, Optional[str]]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "customer_id": str(self.customer_id) if self.customer_id else None,
        }
