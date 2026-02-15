"""
BulkOperation and BulkOperationResult models.

Tracks large-scale batch tool executions (e.g., enriching 14K contacts via
web_search).  The ``BulkOperation`` stores metadata and progress, while each
``BulkOperationResult`` holds the per-item input/output.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.organization import Organization
    from models.user import User

# Status values
BulkOperationStatus = Literal[
    "pending",     # Created, not yet started
    "running",     # Coordinator has fanned out tasks
    "paused",      # Manually paused / awaiting resume
    "completed",   # All items processed
    "failed",      # Coordinator-level failure
]


class BulkOperation(Base):
    """
    Tracks a batch tool execution (e.g., run web_search over 14K contacts).

    The coordinator Celery task creates this record, fans out per-item tasks,
    and updates progress.  The agent monitors via ``monitor_operation``.
    """

    __tablename__ = "bulk_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )

    # --- Operation definition ---
    operation_name: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    params_template: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
    )
    items_query: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200,
    )

    # --- Context for progress broadcasting ---
    conversation_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
    )
    tool_call_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
    )

    # --- Progress ---
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
    )
    total_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    completed_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    succeeded_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    failed_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # --- Celery task ID for the coordinator (useful for revocation) ---
    celery_task_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
    )

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # --- Error (coordinator-level) ---
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    results: Mapped[list["BulkOperationResult"]] = relationship(
        "BulkOperationResult",
        back_populates="bulk_operation",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for API / tool responses."""
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "operation_name": self.operation_name,
            "tool_name": self.tool_name,
            "status": self.status,
            "total_items": self.total_items,
            "completed_items": self.completed_items,
            "succeeded_items": self.succeeded_items,
            "failed_items": self.failed_items,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


class BulkOperationResult(Base):
    """
    Stores the result of a single item processed by a bulk operation.

    One row per item — the ``item_data`` is the rendered input and
    ``result_data`` is the raw tool response.
    """

    __tablename__ = "bulk_operation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    bulk_operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bulk_operations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_index: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )

    # The item dict that was used to render params_template
    item_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
    )
    # Raw tool result
    result_data: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True,
    )

    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    # Relationships
    bulk_operation: Mapped["BulkOperation"] = relationship(
        "BulkOperation", back_populates="results",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "bulk_operation_id": str(self.bulk_operation_id),
            "item_index": self.item_index,
            "item_data": self.item_data,
            "result_data": self.result_data,
            "success": self.success,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
