"""
Integration model for tracking connected integrations.

With Nango, we don't store OAuth tokens ourselves - Nango handles that.
This model just tracks which integrations are connected and their sync status.

All integrations are user-scoped (each user connects with their own credentials).
Sharing flags control what other team members can access:
- share_synced_data: Team can see synced records (deals, contacts, etc.)
- share_query_access: Team can query live data via this connection
- share_write_access: Team can write data via this connection (rare)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class Integration(Base):
    """
    Integration model for tracking connected integrations.

    Nango handles OAuth tokens and credentials.
    We store the nango_connection_id to retrieve credentials when needed.

    All integrations are user-scoped. Sharing flags control team access:
    - share_synced_data: Others can see synced data (default true for CRMs)
    - share_query_access: Others can query live data via this connection
    - share_write_access: Others can write via this connection (almost always false)
    """

    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "provider", "user_id",
            name="uq_integration_org_provider_user"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )

    # Integration type: 'hubspot', 'slack', 'google_calendar', 'salesforce', 'gmail', etc.
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # Owner of this integration (who authenticated)
    # NOTE: nullable=True for backwards compatibility during migration.
    # New code always sets user_id; Phase 2 migration will make it NOT NULL.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=True, index=True
    )

    # DEPRECATED: scope column kept for backwards compatibility with old clients.
    # All new integrations are user-scoped. Will be dropped in Phase 2 migration.
    scope: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Sharing flags - control what team members can access
    share_synced_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    share_query_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    share_write_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # True until user configures sharing preferences after OAuth
    pending_sharing_config: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Nango connection ID - always user-scoped format: "{org_id}:user:{user_id}"
    nango_connection_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # User who connected this integration (same as user_id, kept for audit trail)
    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=True
    )

    # Status
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Additional provider-specific data
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Sync statistics - counts of objects synced (e.g., {"accounts": 5, "deals": 10})
    sync_stats: Mapped[dict[str, int] | None] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )

    def to_dict(self, include_sharing: bool = False) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        result: dict[str, Any] = {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "provider": self.provider,
            "user_id": str(self.user_id),
            "is_active": self.is_active,
            "last_sync_at": f"{self.last_sync_at.isoformat()}Z" if self.last_sync_at else None,
            "last_error": self.last_error,
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
            "sync_stats": self.sync_stats,
        }
        if include_sharing:
            result.update({
                "share_synced_data": self.share_synced_data,
                "share_query_access": self.share_query_access,
                "share_write_access": self.share_write_access,
                "pending_sharing_config": self.pending_sharing_config,
                "connected_by_user_id": str(self.connected_by_user_id) if self.connected_by_user_id else None,
            })
        return result
