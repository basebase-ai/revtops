"""
Integration model for tracking connected integrations.

With Nango, we don't store OAuth tokens ourselves - Nango handles that.
This model just tracks which integrations are connected and their sync status.

Integrations can be either:
- Organization-scoped: One connection shared by all users (e.g., HubSpot, Salesforce)
- User-scoped: Each user connects individually (e.g., Gmail, Calendar)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class Integration(Base):
    """
    Integration model for tracking connected integrations.

    Nango handles OAuth tokens and credentials.
    We store the nango_connection_id to retrieve credentials when needed.
    
    Scope determines whether this is an org-level or user-level integration:
    - 'organization': One connection for the entire org (CRMs like HubSpot, Salesforce)
    - 'user': Each user connects individually (email/calendar like Gmail, Outlook)
    """

    __tablename__ = "integrations"
    __table_args__ = (
        # Unique constraint: one integration per (org, provider, user)
        # For org-scoped: user_id is NULL, so one per org
        # For user-scoped: each user gets their own row
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
    
    # Scope: 'organization' (shared) or 'user' (per-user)
    scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="organization"
    )
    
    # For user-scoped integrations, which user owns this connection
    # NULL for organization-scoped integrations
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=True, index=True
    )

    # Nango connection ID - format depends on scope:
    # - Organization-scoped: "{org_id}"
    # - User-scoped: "{org_id}:user:{user_id}"
    nango_connection_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # User who connected this integration (for org-scoped, tracks who set it up)
    connected_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=True
    )

    # Status
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Additional provider-specific data
    extra_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Sync statistics - counts of objects synced (e.g., {"accounts": 5, "deals": 10})
    sync_stats: Mapped[Optional[dict[str, int]]] = mapped_column(JSONB, nullable=True)

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
            "provider": self.provider,
            "is_active": self.is_active,
            "last_sync_at": f"{self.last_sync_at.isoformat()}Z" if self.last_sync_at else None,
            "last_error": self.last_error,
            "created_at": f"{self.created_at.isoformat()}Z" if self.created_at else None,
            "sync_stats": self.sync_stats,
        }
