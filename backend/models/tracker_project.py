"""
Tracker Project model - projects from issue tracking providers.

Maps to:
- Linear: Projects (cross-team groupings of issues)
- Jira: Epics or Boards
- Asana: Projects
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class TrackerProject(Base):
    """A project from a connected issue tracker."""

    __tablename__ = "tracker_projects"
    __table_args__ = (
        Index("idx_tracker_projects_organization", "organization_id"),
        Index("idx_tracker_projects_source_system", "source_system"),
        Index(
            "uq_tracker_projects_org_source",
            "organization_id",
            "source_system",
            "source_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )

    # Provider discriminator
    source_system: Mapped[str] = mapped_column(String(30), nullable=False)

    # External ID from the provider
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status: planned, started, paused, completed, cancelled (varies by provider)
    state: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    progress: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Dates
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Metadata
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    lead_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    team_ids: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "source_system": self.source_system,
            "source_id": self.source_id,
            "name": self.name,
            "description": self.description,
            "state": self.state,
            "progress": self.progress,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "url": self.url,
            "lead_name": self.lead_name,
            "team_ids": self.team_ids,
            "created_at": (
                f"{self.created_at.isoformat()}Z" if self.created_at else None
            ),
        }
