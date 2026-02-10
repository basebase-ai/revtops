"""
Slack user mapping model for high-confidence RevTops <-> Slack identity links.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class SlackUserMapping(Base):
    """Persisted mapping between RevTops users and Slack users."""

    __tablename__ = "user_mappings_for_identity"
    __table_args__ = (
        Index(
            "uq_identity_mappings_org_user_external_user_source",
            "organization_id",
            "user_id",
            "external_userid",
            "source",
            unique=True,
        ),
        Index(
            "ix_identity_mappings_org_external_user_source",
            "organization_id",
            "external_userid",
            "source",
        ),
        Index(
            "ix_identity_mappings_org_user",
            "organization_id",
            "user_id",
        ),
        Index(
            "ix_identity_mappings_org_external_email_source",
            "organization_id",
            "external_email",
            "source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    revtops_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    external_userid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    external_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="slack")
    match_source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
