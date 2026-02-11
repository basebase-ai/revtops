"""
GoogleDriveFile model — stores synced metadata for files in a user's Google Drive.

This enables the agent to search files by name without hitting the Google API
on every query.  Actual file content is fetched on demand via the connector.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class GoogleDriveFile(Base):
    """A file or folder synced from a user's Google Drive."""

    __tablename__ = "google_drive_files"
    __table_args__ = (
        Index("idx_gdrive_org_user", "organization_id", "user_id"),
        Index(
            "uq_gdrive_org_user_fileid",
            "organization_id",
            "user_id",
            "google_file_id",
            unique=True,
        ),
        Index("idx_gdrive_name_trgm", "name"),  # for ILIKE searches
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Google identifiers
    google_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Hierarchy
    parent_google_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False, default="/")

    # Metadata
    web_view_link: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    google_modified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Sync tracking
    synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API / agent responses."""
        return {
            "google_file_id": self.google_file_id,
            "name": self.name,
            "mime_type": self.mime_type,
            "folder_path": self.folder_path,
            "web_view_link": self.web_view_link,
            "file_size": self.file_size,
            "google_modified_at": (
                f"{self.google_modified_at.isoformat()}Z"
                if self.google_modified_at
                else None
            ),
            "synced_at": (
                f"{self.synced_at.isoformat()}Z" if self.synced_at else None
            ),
        }
