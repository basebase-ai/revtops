"""
Conversation model for grouping chat messages.

Conversations can be:
- type='agent': Interactive user chat sessions
- type='workflow': Automated workflow execution (visible as chat)

Sources:
- source='web': From the web chat interface (has user_id)
- source='slack': From Slack DMs (user_id is NULL, uses source_user_id)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

# Conversation types
ConversationType = Literal["agent", "workflow"]

# Conversation sources
ConversationSource = Literal["web", "slack"]


class Conversation(Base):
    """Conversation model for grouping chat messages."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_source_channel", "source", "source_channel_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # user_id is nullable for Slack conversations where we don't know the RevTops user
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True
    )
    
    # Source tracking for multi-channel conversations
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="web"
    )  # "web" | "slack"
    source_channel_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # Slack DM channel ID
    source_user_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # External user ID (e.g., Slack user ID)
    
    # Conversation type: 'agent' for interactive, 'workflow' for automated
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="agent", index=True
    )
    
    # For workflow conversations, link to the workflow that triggered it
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"), 
        nullable=True, index=True
    )
    
    # For child workflow conversations, link to parent conversation
    parent_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    # Scope for change sessions: root of this conversation tree (same for parent + all child workflows).
    # Null for ad-hoc chats; set for workflow convs so one run = one change session.
    root_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True, index=True
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
    workflow: Mapped[Optional["Workflow"]] = relationship(
        "Workflow", back_populates="conversations"
    )
    change_sessions: Mapped[list["ChangeSession"]] = relationship(
        "ChangeSession", back_populates="conversation"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        result: dict[str, Any] = {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "type": self.type,
            "source": self.source,
            "title": self.title,
            "summary": self.summary,
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
            "updated_at": f"{self.updated_at.isoformat()}Z" if self.updated_at else None,
        }
        if self.workflow_id:
            result["workflow_id"] = str(self.workflow_id)
        if self.parent_conversation_id:
            result["parent_conversation_id"] = str(self.parent_conversation_id)
        if self.root_conversation_id:
            result["root_conversation_id"] = str(self.root_conversation_id)
        if self.source_channel_id:
            result["source_channel_id"] = self.source_channel_id
        if self.source_user_id:
            result["source_user_id"] = self.source_user_id
        return result
    
    @property
    def is_workflow(self) -> bool:
        """Check if this is a workflow conversation."""
        return self.type == "workflow"


# Import for type hints (avoid circular import at runtime)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from models.chat_message import ChatMessage
    from models.workflow import Workflow
    from models.change_session import ChangeSession
