"""
GitHub Commit model - tracks commits on monitored repositories.

Commits are mapped to internal users by matching author email.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.github_repository import GitHubRepository
    from models.user import User


class GitHubCommit(Base):
    """A commit on a tracked GitHub repository."""

    __tablename__ = "github_commits"
    __table_args__ = (
        Index("idx_gh_commits_organization", "organization_id"),
        Index("idx_gh_commits_repository", "repository_id"),
        Index("idx_gh_commits_author_date", "author_date"),
        Index("idx_gh_commits_user", "user_id"),
        Index(
            "uq_gh_commits_org_sha",
            "organization_id",
            "repository_id",
            "sha",
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

    # Commit data
    sha: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Author info (from Git)
    author_name: Mapped[str] = mapped_column(String(255), nullable=False)
    author_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    author_login: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # GitHub username
    author_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Committer info (may differ from author in rebases, cherry-picks, etc.)
    committer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    committer_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    committed_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    # Stats
    additions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deletions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    changed_files: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Links
    url: Mapped[str] = mapped_column(String(512), nullable=False)

    # Mapped internal user (resolved by email matching)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=True
    )

    # Timestamps
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # Relationships
    repository: Mapped["GitHubRepository"] = relationship(
        "GitHubRepository", back_populates="commits"
    )
    user: Mapped[Optional["User"]] = relationship("User")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "repository_id": str(self.repository_id),
            "sha": self.sha,
            "message": self.message,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "author_login": self.author_login,
            "author_date": (
                f"{self.author_date.isoformat()}Z" if self.author_date else None
            ),
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_files": self.changed_files,
            "url": self.url,
            "user_id": str(self.user_id) if self.user_id else None,
        }
