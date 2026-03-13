"""
Generic messenger user mapping model.

Maps an external identity on any messenger platform to a RevTops user.
Replaces the Slack-specific ``SlackUserMapping`` / ``user_mappings_for_identity``
table with a platform-generic design that works for Slack, Teams, Discord,
SMS, WhatsApp, and any future messenger.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class MessengerUserMapping(Base):
    """Maps an external messenger identity to a RevTops user."""

    __tablename__ = "messenger_user_mappings"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "workspace_id",
            "external_user_id",
            name="uq_messenger_user_mappings_platform_ws_extid",
        ),
        Index(
            "ix_messenger_user_mappings_platform_extid",
            "platform",
            "external_user_id",
        ),
        Index(
            "ix_messenger_user_mappings_user_id",
            "user_id",
        ),
        Index(
            "ix_messenger_user_mappings_org_platform",
            "organization_id",
            "platform",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    workspace_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    external_user_id: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_email: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    match_source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
