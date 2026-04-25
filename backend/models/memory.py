"""Memory model for persistent agent memories/preferences across multiple scopes."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class Memory(Base):
    """Stores persistent memories/preferences scoped to user/job/channel contexts."""

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_entity", "entity_type", "entity_id"),
        Index(
            "ix_memories_scope_lookup",
            "organization_id",
            "scope_type",
            "scope_source",
            "scope_channel_id",
            "category",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entity_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # 'user' | 'organization_member'
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # PK of the associated entity (nullable for non-UUID scopes like channels)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'personal', 'preference', 'professional', 'project', etc.
    scope_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # 'channel' for channel-scoped memory records
    scope_source: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # e.g. 'slack', 'web'
    scope_channel_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # normalized source channel/thread identifier
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )
