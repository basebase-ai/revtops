"""
Meeting model - canonical representation of real-world meetings.

A Meeting is the normalized, deduplicated entity that represents a single
real-world meeting. Multiple calendar events (from different attendees' calendars)
and transcripts (from various services like Fireflies, Otter, etc.) link back
to the same Meeting.

This allows agents to query meetings as first-class entities without worrying
about which calendar system or transcription service the data came from.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.account import Account
    from models.activity import Activity
    from models.deal import Deal


class Meeting(Base):
    """
    Meeting model representing canonical real-world meetings.
    
    Calendar events, transcripts, and notes all link to this entity
    via the meeting_id foreign key in the activities table.
    """

    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )

    # Core meeting info (normalized from best available source)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scheduled_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Participants (deduplicated across all sources)
    # Format: [{email: str, name: str, is_organizer: bool, rsvp_status: str}]
    participants: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    organizer_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    participant_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Status: scheduled, completed, cancelled
    status: Mapped[str] = mapped_column(String(50), default="scheduled", nullable=False)

    # Aggregated content from transcripts/notes
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_items: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    key_topics: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)

    # Full transcript (optional, can be large)
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Links to related entities
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    deal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=True
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    account: Mapped[Optional["Account"]] = relationship("Account")
    deal: Mapped[Optional["Deal"]] = relationship("Deal")
    activities: Mapped[list["Activity"]] = relationship(
        "Activity", back_populates="meeting"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "title": self.title,
            "scheduled_start": f"{self.scheduled_start.isoformat()}Z" if self.scheduled_start else None,
            "scheduled_end": f"{self.scheduled_end.isoformat()}Z" if self.scheduled_end else None,
            "duration_minutes": self.duration_minutes,
            "participants": self.participants,
            "organizer_email": self.organizer_email,
            "participant_count": self.participant_count,
            "status": self.status,
            "summary": self.summary,
            "action_items": self.action_items,
            "key_topics": self.key_topics,
            "account_id": str(self.account_id) if self.account_id else None,
            "deal_id": str(self.deal_id) if self.deal_id else None,
        }

    def to_search_dict(self) -> dict[str, Any]:
        """Simplified dict for search results."""
        return {
            "id": str(self.id),
            "title": self.title,
            "scheduled_start": f"{self.scheduled_start.isoformat()}Z" if self.scheduled_start else None,
            "duration_minutes": self.duration_minutes,
            "participant_count": self.participant_count,
            "status": self.status,
            "has_summary": bool(self.summary),
            "has_action_items": bool(self.action_items),
        }
