"""Model for Slack bot installs (Add-to-Slack flow) — one row per workspace."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class SlackBotInstall(Base):
    """Stores bot token for a workspace that added Basebase via the public Add-to-Slack link."""

    __tablename__ = "slack_bot_installs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), nullable=False)
