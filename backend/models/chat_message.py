"""
ChatMessage model for storing conversation history.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class ChatMessage(Base):
    """ChatMessage model for storing conversation history."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # Store tool invocations
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "role": self.role,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
