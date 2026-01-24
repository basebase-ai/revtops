"""
ChatMessage model for storing conversation history.

Content blocks follow the Anthropic API pattern:
- {"type": "text", "text": "..."}
- {"type": "tool_use", "id": "...", "name": "...", "input": {...}, "result": {...}, "status": "complete"}
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.conversation import Conversation


class ChatMessage(Base):
    """ChatMessage model for storing conversation history."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'user' or 'assistant'
    
    # New: Array of content blocks [{type: 'text'|'tool_use', ...}]
    content_blocks: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    
    # Legacy fields - kept for backwards compatibility during migration
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # Relationships
    conversation: Mapped[Optional["Conversation"]] = relationship(
        "Conversation", back_populates="messages"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        # Use content_blocks if available and non-empty, otherwise convert legacy format
        blocks = self.content_blocks
        if not blocks:  # Handles None, empty list, etc.
            blocks = self._legacy_to_blocks()
        
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "role": self.role,
            "content_blocks": blocks,
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
        }
    
    def _legacy_to_blocks(self) -> list[dict[str, Any]]:
        """Convert legacy content + tool_calls to content_blocks format."""
        blocks: list[dict[str, Any]] = []
        
        # Add text content if present
        if self.content and self.content.strip():
            blocks.append({"type": "text", "text": self.content})
        
        # Add tool calls if present
        if self.tool_calls:
            for tc in self.tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "input": tc.get("input", {}),
                    "result": tc.get("result"),
                    "status": "complete",
                })
        
        return blocks
