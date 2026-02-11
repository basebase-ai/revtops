"""
GitHub Repository model - tracks which repos an org is monitoring.

Teams select specific repos to track; only those repos get synced.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.github_commit import GitHubCommit
    from models.github_pull_request import GitHubPullRequest


class GitHubRepository(Base):
    """A GitHub repository being tracked by an organization."""

    __tablename__ = "github_repositories"
    __table_args__ = (
        Index("idx_gh_repos_organization", "organization_id"),
        Index(
            "uq_gh_repos_org_github_id",
            "organization_id",
            "github_repo_id",
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

    # GitHub identifiers
    github_repo_id: Mapped[int] = mapped_column(Integer, nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. "octocat"
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. "hello-world"
    full_name: Mapped[str] = mapped_column(
        String(512), nullable=False
    )  # e.g. "octocat/hello-world"

    # Metadata
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_branch: Mapped[str] = mapped_column(
        String(255), nullable=False, default="main"
    )
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    language: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)

    # Tracking state
    is_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )

    # Relationships
    commits: Mapped[list["GitHubCommit"]] = relationship(
        "GitHubCommit", back_populates="repository", cascade="all, delete-orphan"
    )
    pull_requests: Mapped[list["GitHubPullRequest"]] = relationship(
        "GitHubPullRequest", back_populates="repository", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "github_repo_id": self.github_repo_id,
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "description": self.description,
            "default_branch": self.default_branch,
            "is_private": self.is_private,
            "language": self.language,
            "url": self.url,
            "is_tracked": self.is_tracked,
            "last_sync_at": (
                f"{self.last_sync_at.isoformat()}Z" if self.last_sync_at else None
            ),
            "created_at": (
                f"{self.created_at.isoformat()}Z" if self.created_at else None
            ),
        }
