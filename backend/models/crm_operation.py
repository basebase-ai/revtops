"""
CRM Operation model - tracks pending and executed CRM write operations.

Used for the approval flow when the agent creates/updates CRM records.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class CrmOperation(Base):
    """Tracks CRM write operations with approval workflow."""

    __tablename__ = "crm_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    # user_id is nullable for Slack conversations where we don't have a RevTops user
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=True
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )

    # Target system: 'hubspot', 'salesforce'
    target_system: Mapped[str] = mapped_column(String(50), nullable=False)
    # Record type: 'contact', 'company', 'deal'
    record_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Operation: 'create', 'update', 'upsert'
    operation: Mapped[str] = mapped_column(String(20), nullable=False)

    # Status: 'pending', 'approved', 'executing', 'completed', 'failed', 'canceled', 'expired'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # Input data
    input_records: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # Validated/normalized records ready for execution
    validated_records: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # Duplicate warnings found during validation
    duplicate_warnings: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )

    # Execution results
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Counts
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    failure_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        # Set expiry to 30 minutes from now if not provided
        if "expires_at" not in kwargs:
            kwargs["expires_at"] = datetime.utcnow() + timedelta(minutes=30)
        super().__init__(**kwargs)

    @property
    def is_expired(self) -> bool:
        """Check if this operation has expired."""
        return datetime.utcnow() > self.expires_at

    @property
    def can_execute(self) -> bool:
        """Check if this operation can be executed."""
        return self.status == "pending" and not self.is_expired

    def to_preview_dict(self) -> dict[str, Any]:
        """Convert to preview dictionary for frontend."""
        return {
            "operation_id": str(self.id),
            "target_system": self.target_system,
            "record_type": self.record_type,
            "operation": self.operation,
            "status": self.status,
            "records": self.validated_records,
            "duplicate_warnings": self.duplicate_warnings or [],
            "record_count": self.record_count,
            "expires_at": f"{self.expires_at.isoformat()}Z",
        }

    def to_result_dict(self) -> dict[str, Any]:
        """Convert to result dictionary after execution."""
        return {
            "operation_id": str(self.id),
            "status": self.status,
            "target_system": self.target_system,
            "record_type": self.record_type,
            "operation": self.operation,
            "record_count": self.record_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "result": self.result,
            "error_message": self.error_message,
            "executed_at": f"{self.executed_at.isoformat()}Z" if self.executed_at else None,
        }
