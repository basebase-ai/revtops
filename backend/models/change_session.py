"""
ChangeSession model for tracking batches of agent-made changes.

Part of Phase 3: Change Sessions & Rollback
Groups related changes made by an agent task so users can review
and approve/discard them as a unit.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.conversation import Conversation
    from models.organization import Organization
    from models.record_snapshot import RecordSnapshot
    from models.user import User

# Status type
ChangeSessionStatus = Literal["pending", "approved", "discarded"]


class ChangeSession(Base):
    """
    Groups related changes made by an agent task.
    
    When an agent modifies local data (contacts, deals, etc.), changes are
    tracked in a session. Users can then:
    - Approve: Finalize the changes
    - Discard: Rollback all changes using stored snapshots
    
    This provides a safety net for AI-assisted data modifications.
    """
    
    __tablename__ = "change_sessions"
    
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    user_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    conversation_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
    )
    
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    resolved_by: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="change_sessions",
    )
    
    user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="change_sessions",
    )
    
    resolved_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[resolved_by],
    )
    
    conversation: Mapped[Optional["Conversation"]] = relationship(
        "Conversation",
        back_populates="change_sessions",
    )
    
    snapshots: Mapped[list["RecordSnapshot"]] = relationship(
        "RecordSnapshot",
        back_populates="change_session",
        cascade="all, delete-orphan",
    )
    
    def __repr__(self) -> str:
        return f"<ChangeSession {self.id} status={self.status}>"
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        result: dict[str, Any] = {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if self.user_id:
            result["user_id"] = str(self.user_id)
        if self.conversation_id:
            result["conversation_id"] = str(self.conversation_id)
        if self.description:
            result["description"] = self.description
        if self.resolved_at:
            result["resolved_at"] = self.resolved_at.isoformat()
        if self.resolved_by:
            result["resolved_by"] = str(self.resolved_by)
        return result
    
    @property
    def is_pending(self) -> bool:
        """Check if this session is still pending approval."""
        return self.status == "pending"
    
    @property
    def is_resolved(self) -> bool:
        """Check if this session has been resolved (approved or discarded)."""
        return self.status in ("approved", "discarded")
