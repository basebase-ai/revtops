"""
Artifact model - saved analyses, dashboards, reports, and downloadable files.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


# Type alias for artifact content types
ArtifactContentType = Literal["text", "markdown", "pdf", "chart"]


class Artifact(Base):
    """Artifact model for saved analyses, dashboards, reports, and downloadable files."""

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )

    type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'dashboard', 'report', 'analysis'
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # Dashboard structure/queries
    snapshot_data: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # Data at creation time
    is_live: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )  # Refresh on load vs static

    # New fields for file-based artifacts
    content: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Text/markdown content or base64-encoded PDF
    content_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'text', 'markdown', 'pdf', 'chart'
    mime_type: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # e.g., 'text/plain', 'application/pdf'
    filename: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # Original filename for download

    # Link to conversation for persistence
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Link to specific message
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    last_viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def to_dict(self, include_content: bool = False) -> dict[str, Any]:
        """Convert to dictionary for API responses.
        
        Args:
            include_content: Whether to include the full content field.
                            Set to False for list views to reduce payload size.
        """
        result: dict[str, Any] = {
            "id": str(self.id),
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "config": self.config,
            "snapshot_data": self.snapshot_data,
            "is_live": self.is_live,
            "content_type": self.content_type,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "message_id": str(self.message_id) if self.message_id else None,
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
            "user_id": str(self.user_id),
        }
        if include_content:
            result["content"] = self.content
        return result
