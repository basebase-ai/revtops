"""
Generic messenger bot install model.

Stores bot/app credentials for a workspace on any messenger platform.
Replaces the Slack-specific ``SlackBotInstall`` / ``slack_bot_installs``
table with a platform-generic design.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class MessengerBotInstall(Base):
    """Stores bot credentials for a messenger workspace."""

    __tablename__ = "messenger_bot_installs"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "workspace_id",
            name="uq_messenger_bot_installs_platform_ws",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_token_encrypted: Mapped[str] = mapped_column(
        Text(), nullable=False
    )
    extra_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now(), nullable=False
    )
