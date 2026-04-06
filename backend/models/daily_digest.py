"""
Daily per-member digest — LLM summary of a user's activity for one calendar day (PT).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.organization import Organization
    from models.user import User


class DailyDigest(Base):
    """Stored digest for one org member and one digest calendar date (America/Los_Angeles)."""

    __tablename__ = "daily_digests"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "digest_date",
            name="uq_daily_digests_org_user_date",
        ),
        Index("ix_daily_digests_org_date", "organization_id", "digest_date"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    digest_date: Mapped[date] = mapped_column(Date, nullable=False)

    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship("Organization")
    user: Mapped["User"] = relationship("User")
