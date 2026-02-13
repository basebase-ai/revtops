"""
AgentTask model - tracks background agent task execution.

Used for persistent agent processes that continue running even when
browser tabs are closed. Clients subscribe to task updates via WebSocket.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class AgentTask(Base):
    """Tracks background agent task execution with persistence."""

    __tablename__ = "agent_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Status: 'running', 'completed', 'failed', 'cancelled'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")

    # The user message that triggered this task
    user_message: Mapped[str] = mapped_column(Text, nullable=False)

    # Output chunks for streaming catchup - array of {type, data, timestamp}
    # Types: 'text_delta', 'tool_use', 'tool_result', 'thinking', 'done', 'error'
    output_chunks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # Error information if task failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    # Indexes for common queries
    __table_args__ = (
        Index("ix_agent_tasks_user_status", "user_id", "status"),
        Index("ix_agent_tasks_conversation", "conversation_id"),
        Index("ix_agent_tasks_org_status", "organization_id", "status"),
    )

    @property
    def is_running(self) -> bool:
        """Check if this task is still running."""
        return self.status == "running"

    @property
    def chunk_count(self) -> int:
        """Get the number of output chunks."""
        return len(self.output_chunks) if self.output_chunks else 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id),
            "user_id": str(self.user_id),
            "organization_id": str(self.organization_id),
            "status": self.status,
            "user_message": self.user_message,
            "chunk_count": self.chunk_count,
            "error_message": self.error_message,
            "started_at": f"{self.started_at.isoformat()}Z" if self.started_at else None,
            "completed_at": f"{self.completed_at.isoformat()}Z" if self.completed_at else None,
            "last_activity_at": f"{self.last_activity_at.isoformat()}Z" if self.last_activity_at else None,
        }

    def to_state_dict(self) -> dict[str, Any]:
        """Convert to state dictionary including output chunks for catchup."""
        result = self.to_dict()
        result["output_chunks"] = self.output_chunks or []
        return result
