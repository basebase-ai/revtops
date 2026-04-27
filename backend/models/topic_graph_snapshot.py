from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class TopicGraphSnapshot(Base):
    """Daily cached org graph snapshot for Graph Magic."""

    __tablename__ = "topic_graph_snapshots"
    __table_args__ = (
        UniqueConstraint("organization_id", "graph_date", name="uq_topic_graph_org_date"),
        Index("ix_topic_graph_org_date", "organization_id", "graph_date"),
        Index("ix_topic_graph_graph_date", "graph_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    graph_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed", server_default="completed")
    graph_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    run_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
