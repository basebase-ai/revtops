"""
Conversation model for grouping chat messages.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base


class Conversation(Base):
    """Conversation model for grouping chat messages."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # Auto-generated from first message
    summary: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Optional AI-generated summary
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Cached fields for fast list queries (denormalized)
    message_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    last_message_preview: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )

    # Relationships
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="conversation", order_by="ChatMessage.created_at"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "title": self.title,
            "summary": self.summary,
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
            "updated_at": f"{self.updated_at.isoformat()}Z" if self.updated_at else None,
        }


# Import ChatMessage for type hints (avoid circular import at runtime)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from models.chat_message import ChatMessage
