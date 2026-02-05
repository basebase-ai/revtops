"""
RecordSnapshot model for storing before/after state of records.

Part of Phase 3: Change Sessions & Rollback
Stores the state of a record before/after modification, enabling
rollback if the user discards changes.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.change_session import ChangeSession

# Operation type
SnapshotOperation = Literal["create", "update", "delete"]


class RecordSnapshot(Base):
    """
    Stores the before/after state of a record for rollback capability.
    
    When an agent modifies a record:
    - For creates: before_data is null, after_data has the new record
    - For updates: before_data has old state, after_data has new state
    - For deletes: before_data has old state, after_data is null
    
    On discard, we restore before_data (or delete the record for creates).
    """
    
    __tablename__ = "record_snapshots"
    
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    
    change_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("change_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    table_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    
    record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    
    operation: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    
    before_data: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )
    
    after_data: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    
    # Relationships
    change_session: Mapped["ChangeSession"] = relationship(
        "ChangeSession",
        back_populates="snapshots",
    )
    
    def __repr__(self) -> str:
        return f"<RecordSnapshot {self.table_name}:{self.record_id} op={self.operation}>"
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        result: dict[str, Any] = {
            "id": str(self.id),
            "change_session_id": str(self.change_session_id),
            "table_name": self.table_name,
            "record_id": str(self.record_id),
            "operation": self.operation,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if self.before_data:
            result["before_data"] = self.before_data
        if self.after_data:
            result["after_data"] = self.after_data
        return result
    
    @property
    def is_create(self) -> bool:
        """Check if this is a create operation."""
        return self.operation == "create"
    
    @property
    def is_update(self) -> bool:
        """Check if this is an update operation."""
        return self.operation == "update"
    
    @property
    def is_delete(self) -> bool:
        """Check if this is a delete operation."""
        return self.operation == "delete"
