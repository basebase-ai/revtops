"""
Contact model - normalized representation of contacts.
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
    from models.account import Account


class Contact(Base):
    """Contact model representing people associated with accounts."""

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(
        String(50), default="salesforce", nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # Relationships
    account: Mapped[Optional["Account"]] = relationship(
        "Account", back_populates="contacts"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "email": self.email,
            "title": self.title,
            "phone": self.phone,
            "account_id": str(self.account_id) if self.account_id else None,
            "source_id": self.source_id,
            "source_system": self.source_system,
        }
