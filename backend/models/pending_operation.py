"""
Pending Operation model - tracks pending tool executions requiring approval.

Generalized from CrmOperation to support any tool that requires user approval.
This enables the same approval flow for CRM writes, emails, Slack posts, etc.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.database import Base


class PendingOperation(Base):
    """Tracks pending tool operations that require user approval."""

    __tablename__ = "pending_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )

    # Tool identification
    tool_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tool_params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # CRM-specific fields (for backward compatibility with crm_write)
    # These are nullable for non-CRM tools
    target_system: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    record_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    operation: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Status: 'pending', 'approved', 'executing', 'completed', 'failed', 'canceled', 'expired'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # CRM-specific input data (for backward compatibility)
    input_records: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    validated_records: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    duplicate_warnings: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )

    # Execution results
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Counts (for CRM operations)
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
        # Set tool_name from old-style operations
        if "tool_name" not in kwargs and kwargs.get("target_system"):
            kwargs["tool_name"] = "crm_write"
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
        base: dict[str, Any] = {
            "operation_id": str(self.id),
            "tool_name": self.tool_name,
            "status": self.status,
            "expires_at": f"{self.expires_at.isoformat()}Z",
        }
        
        # CRM-specific fields
        if self.tool_name == "crm_write":
            base.update({
                "target_system": self.target_system,
                "record_type": self.record_type,
                "operation": self.operation,
                "records": self.validated_records,
                "duplicate_warnings": self.duplicate_warnings or [],
                "record_count": self.record_count,
            })
        else:
            base["params"] = self.tool_params
        
        return base

    def to_result_dict(self) -> dict[str, Any]:
        """Convert to result dictionary after execution."""
        base: dict[str, Any] = {
            "operation_id": str(self.id),
            "tool_name": self.tool_name,
            "status": self.status,
            "result": self.result,
            "error_message": self.error_message,
            "executed_at": f"{self.executed_at.isoformat()}Z" if self.executed_at else None,
        }
        
        # CRM-specific fields
        if self.tool_name == "crm_write":
            base.update({
                "target_system": self.target_system,
                "record_type": self.record_type,
                "operation": self.operation,
                "record_count": self.record_count,
                "success_count": self.success_count,
                "failure_count": self.failure_count,
            })
        
        return base


# Alias for backward compatibility
CrmOperation = PendingOperation
