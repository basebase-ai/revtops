"""
Tracker Team model - teams/workspaces from issue tracking providers.

Maps to:
- Linear: Teams (e.g. "Engineering")
- Jira: Projects (the top-level container)
- Asana: Teams/Workspaces
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class TrackerTeam(Base):
    """A team/project from a connected issue tracker."""

    __tablename__ = "tracker_teams"
    __table_args__ = (
        Index("idx_tracker_teams_organization", "organization_id"),
        Index("idx_tracker_teams_source_system", "source_system"),
        Index(
            "uq_tracker_teams_org_source",
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
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id"), nullable=False
    )

    # Provider discriminator: "linear", "jira", "asana"
    source_system: Mapped[str] = mapped_column(String(30), nullable=False)

    # External ID from the provider
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # e.g. "ENG" in Linear, "PROJ" in Jira
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
            "key": self.key,
            "description": self.description,
            "created_at": (
                f"{self.created_at.isoformat()}Z" if self.created_at else None
            ),
        }
