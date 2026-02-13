"""
User Tool Setting model for storing per-user approval preferences.

Part of the unified tools architecture where users can configure which
tools require approval vs. auto-approve (like Cursor's yolo mode).
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.user import User


class UserToolSetting(Base):
    """
    Stores per-user auto-approve settings for tools.
    
    By default, EXTERNAL_WRITE tools (crm_write, send_email_from, send_slack)
    require approval. Users can enable auto-approve to skip the approval step.
    
    This is similar to Cursor's "yolo mode" - giving users control over
    the speed vs. safety tradeoff.
    """
    
    __tablename__ = "user_tool_settings"
    
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
    
    tool_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    
    auto_approve: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="tool_settings")
    
    def __repr__(self) -> str:
        return f"<UserToolSetting {self.user_id}:{self.tool_name}={self.auto_approve}>"
