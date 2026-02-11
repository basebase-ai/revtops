"""
SharedFile model — stores synced metadata for files from any external source.

Supported sources: Google Drive, Airtable, OneDrive, etc.

This enables the agent to search files by name without hitting the source API
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


class SharedFile(Base):
    """A file or folder synced from an external source (Google Drive, Airtable, etc.)."""

    __tablename__ = "shared_files"
    __table_args__ = (
        Index("idx_shared_files_org_user", "organization_id", "user_id"),
        Index(
            "uq_shared_files_org_user_source_extid",
            "organization_id",
            "user_id",
            "source",
            "external_id",
            unique=True,
        ),
        Index("idx_shared_files_name_trgm", "name"),  # for ILIKE searches
        Index("idx_shared_files_source", "source"),
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

    # Source identifier (e.g. "google_drive", "airtable", "onedrive")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="google_drive")

    # External identifiers
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Hierarchy
    parent_external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False, default="/")

    # Metadata
    web_view_link: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_modified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Sync tracking
    synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API / agent responses."""
        return {
            "external_id": self.external_id,
            "source": self.source,
            "name": self.name,
            "mime_type": self.mime_type,
            "folder_path": self.folder_path,
            "web_view_link": self.web_view_link,
            "file_size": self.file_size,
            "source_modified_at": (
                f"{self.source_modified_at.isoformat()}Z"
                if self.source_modified_at
                else None
            ),
            "synced_at": (
                f"{self.synced_at.isoformat()}Z" if self.synced_at else None
            ),
        }
