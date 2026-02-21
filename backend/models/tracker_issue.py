"""
Tracker Issue model - issues/tasks from issue tracking providers.

Maps to:
- Linear: Issues (e.g. ENG-123)
- Jira: Issues (e.g. PROJ-456)
- Asana: Tasks
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.tracker_project import TrackerProject
    from models.tracker_team import TrackerTeam


class TrackerIssue(Base):
    """An issue/task from a connected issue tracker."""

    __tablename__ = "tracker_issues"
    __table_args__ = (
        Index("idx_tracker_issues_organization", "organization_id"),
        Index("idx_tracker_issues_source_system", "source_system"),
        Index("idx_tracker_issues_team", "team_id"),
        Index("idx_tracker_issues_project", "project_id"),
        Index("idx_tracker_issues_state_type", "state_type"),
        Index("idx_tracker_issues_assignee", "assignee_name"),
        Index("idx_tracker_issues_created_date", "created_date"),
        Index("idx_tracker_issues_user", "user_id"),
        Index(
            "uq_tracker_issues_org_source",
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
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracker_teams.id"), nullable=False
    )

    # Provider discriminator
    source_system: Mapped[str] = mapped_column(String(30), nullable=False)

    # External ID from the provider
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Human-readable identifier: "ENG-123" (Linear), "PROJ-456" (Jira), etc.
    identifier: Mapped[str] = mapped_column(String(30), nullable=False)

    # Content
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # State
    state_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # backlog, unstarted, started, completed, cancelled

    # Priority: 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low
    priority: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    priority_label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Issue type (bug, feature, task, story, epic, subtask, etc.)
    issue_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # People
    assignee_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assignee_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    creator_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Project link (nullable - not all issues belong to a project)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracker_projects.id"), nullable=True
    )

    # Labels stored as JSON array of strings
    labels: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)

    # Estimate (story points / t-shirt size numeric)
    estimate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Links
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # Dates
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Mapped internal user (resolved by email matching)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # Timestamps
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )

    # Relationships
    team: Mapped["TrackerTeam"] = relationship("TrackerTeam")
    project: Mapped[Optional["TrackerProject"]] = relationship("TrackerProject")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        from config import to_iso8601

        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "source_system": self.source_system,
            "source_id": self.source_id,
            "team_id": str(self.team_id),
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description,
            "state_name": self.state_name,
            "state_type": self.state_type,
            "priority": self.priority,
            "priority_label": self.priority_label,
            "issue_type": self.issue_type,
            "assignee_name": self.assignee_name,
            "assignee_email": self.assignee_email,
            "creator_name": self.creator_name,
            "project_id": str(self.project_id) if self.project_id else None,
            "labels": self.labels,
            "estimate": self.estimate,
            "url": self.url,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "created_date": to_iso8601(self.created_date),
            "updated_date": to_iso8601(self.updated_date),
            "completed_date": to_iso8601(self.completed_date),
            "cancelled_date": to_iso8601(self.cancelled_date),
            "user_id": str(self.user_id) if self.user_id else None,
        }
