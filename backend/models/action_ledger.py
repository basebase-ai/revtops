"""
Action Ledger model — write-ahead audit trail for connector mutations.

Every external write (CRM updates, file edits, emails, etc.) gets an INTENT
row before execution and an OUTCOME update after.  Status is derived:
``outcome IS NULL`` = in-flight, ``outcome->>'status'`` = ``"success"`` or
``"error"``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class ActionLedgerEntry(Base):
    """Audit row for a single connector mutation (write or action)."""

    __tablename__ = "action_ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )

    # What was done
    connector: Mapped[str] = mapped_column(String(50), nullable=False)
    dispatch_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "write" | "action"
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Intent (pre-execution) and outcome (post-execution)
    intent: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Reversibility (future phases)
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reversed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("action_ledger.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "workflow_id": str(self.workflow_id) if self.workflow_id else None,
            "connector": self.connector,
            "dispatch_type": self.dispatch_type,
            "operation": self.operation,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "intent": self.intent,
            "outcome": self.outcome,
            "reversible": self.reversible,
            "reversed_at": self.reversed_at.isoformat() if self.reversed_at else None,
            "reversed_by": str(self.reversed_by) if self.reversed_by else None,
            "status": self._derived_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }

    @property
    def _derived_status(self) -> str:
        if self.outcome is None:
            return "in-flight"
        return self.outcome.get("status", "unknown")
