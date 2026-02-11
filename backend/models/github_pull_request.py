"""
GitHub Pull Request model - tracks PRs on monitored repositories.

PRs are mapped to internal users by matching author login/email.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.github_repository import GitHubRepository
    from models.user import User


class GitHubPullRequest(Base):
    """A pull request on a tracked GitHub repository."""

    __tablename__ = "github_pull_requests"
    __table_args__ = (
        Index("idx_gh_prs_organization", "organization_id"),
        Index("idx_gh_prs_repository", "repository_id"),
        Index("idx_gh_prs_state", "state"),
        Index("idx_gh_prs_user", "user_id"),
        Index("idx_gh_prs_created_date", "created_date"),
        Index(
            "uq_gh_prs_org_repo_number",
            "organization_id",
            "repository_id",
            "number",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_repositories.id"), nullable=False
    )

    # PR identification
    github_pr_id: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Content
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # State: "open", "closed", "merged"
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    # Author
    author_login: Mapped[str] = mapped_column(String(255), nullable=False)
    author_avatar_url: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )

    # Merge info
    merged_by_login: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    merge_commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # Dates
    created_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    merged_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Stats
    additions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deletions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    changed_files: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    commits_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Labels & reviewers stored as JSON
    labels: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    reviewers: Mapped[Optional[list[str]]] = mapped_column(
        JSONB, nullable=True
    )  # GitHub logins

    # Links
    url: Mapped[str] = mapped_column(String(512), nullable=False)

    # Mapped internal user (resolved by login/email matching)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # Timestamps
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # Relationships
    repository: Mapped["GitHubRepository"] = relationship(
        "GitHubRepository", back_populates="pull_requests"
    )
    user: Mapped[Optional["User"]] = relationship("User")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "repository_id": str(self.repository_id),
            "github_pr_id": self.github_pr_id,
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "author_login": self.author_login,
            "merged_by_login": self.merged_by_login,
            "created_date": (
                f"{self.created_date.isoformat()}Z" if self.created_date else None
            ),
            "merged_date": (
                f"{self.merged_date.isoformat()}Z" if self.merged_date else None
            ),
            "closed_date": (
                f"{self.closed_date.isoformat()}Z" if self.closed_date else None
            ),
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_files": self.changed_files,
            "commits_count": self.commits_count,
            "labels": self.labels,
            "reviewers": self.reviewers,
            "url": self.url,
            "user_id": str(self.user_id) if self.user_id else None,
        }
