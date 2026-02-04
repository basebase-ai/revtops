"""
Meeting deduplication service.

Provides functionality to:
1. Find existing meetings that match a calendar event or transcript
2. Create new canonical meetings when no match exists
3. Link activities (calendar events, transcripts, notes) to meetings
4. Merge participant lists and aggregate meeting data

The goal is to create one canonical Meeting entity per real-world meeting,
even if that meeting appears in multiple calendars or has multiple transcripts.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.meeting import Meeting
from models.activity import Activity
from models.database import get_session

logger = logging.getLogger(__name__)

# Time window for matching meetings (±10 minutes)
MEETING_TIME_WINDOW_MINUTES = 10

# Minimum participant overlap ratio to consider a match
MIN_PARTICIPANT_OVERLAP = 0.4


def extract_emails_from_participants(participants: list[dict[str, Any]] | None) -> set[str]:
    """Extract lowercase email addresses from a participants list."""
    if not participants:
        return set()
    emails: set[str] = set()
    for p in participants:
        email = p.get("email", "")
        if email:
            emails.add(email.lower().strip())
    return emails


def calculate_participant_overlap(
    participants_a: list[dict[str, Any]] | None,
    participants_b: list[dict[str, Any]] | None,
) -> float:
    """
    Calculate the overlap ratio between two participant lists.
    
    Returns a value between 0.0 (no overlap) and 1.0 (complete overlap).
    Uses Jaccard similarity: |A ∩ B| / |A ∪ B|
    """
    emails_a = extract_emails_from_participants(participants_a)
    emails_b = extract_emails_from_participants(participants_b)
    
    if not emails_a and not emails_b:
        # Both empty - consider it a match (title-based matching will be used)
        return 1.0
    
    if not emails_a or not emails_b:
        # One empty - can't determine overlap
        return 0.0
    
    intersection = emails_a & emails_b
    union = emails_a | emails_b
    
    return len(intersection) / len(union) if union else 0.0


def merge_participants(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """
    Merge two participant lists, deduplicating by email.
    
    Prefers data from 'new' for duplicate entries (assumes newer = better).
    """
    by_email: dict[str, dict[str, Any]] = {}
    
    # Add existing participants first
    for p in (existing or []):
        email = p.get("email", "").lower().strip()
        if email:
            by_email[email] = p
    
    # Override/add with new participants
    for p in (new or []):
        email = p.get("email", "").lower().strip()
        if email:
            by_email[email] = p
    
    return list(by_email.values())


async def find_matching_meeting(
    session: AsyncSession,
    organization_id: UUID,
    scheduled_start: datetime,
    participants: list[dict[str, Any]] | None = None,
    title: str | None = None,
) -> Meeting | None:
    """
    Find an existing meeting that matches the given criteria.
    
    Matching logic:
    1. Same organization
    2. Start time within ±MEETING_TIME_WINDOW_MINUTES
    3. Either:
       a. Participant overlap >= MIN_PARTICIPANT_OVERLAP, or
       b. Exact title match (fallback for when participants aren't available)
    
    Returns the matching Meeting or None if no match found.
    """
    # Normalize to naive UTC datetime for database compatibility
    if scheduled_start.tzinfo is not None:
        scheduled_start = scheduled_start.replace(tzinfo=None)
    
    time_window = timedelta(minutes=MEETING_TIME_WINDOW_MINUTES)
    start_min = scheduled_start - time_window
    start_max = scheduled_start + time_window
    
    # Query meetings in the time window
    result = await session.execute(
        select(Meeting).where(
            and_(
                Meeting.organization_id == organization_id,
                Meeting.scheduled_start >= start_min,
                Meeting.scheduled_start <= start_max,
            )
        )
    )
    candidates = result.scalars().all()
    
    if not candidates:
        return None
    
    # Check each candidate for participant overlap or title match
    for meeting in candidates:
        # Check participant overlap
        overlap = calculate_participant_overlap(meeting.participants, participants)
        if overlap >= MIN_PARTICIPANT_OVERLAP:
            logger.debug(
                "Found meeting match by participants: %s (overlap: %.2f)",
                meeting.id,
                overlap,
            )
            return meeting
        
        # Fallback: exact title match (case-insensitive)
        if title and meeting.title:
            if title.lower().strip() == meeting.title.lower().strip():
                logger.debug(
                    "Found meeting match by title: %s ('%s')",
                    meeting.id,
                    title,
                )
                return meeting
    
    return None


async def find_or_create_meeting(
    organization_id: str | UUID,
    scheduled_start: datetime,
    participants: list[dict[str, Any]] | None = None,
    title: str | None = None,
    scheduled_end: datetime | None = None,
    duration_minutes: int | None = None,
    organizer_email: str | None = None,
    summary: str | None = None,
    action_items: list[dict[str, Any]] | None = None,
    key_topics: list[str] | None = None,
    status: str = "scheduled",
) -> Meeting:
    """
    Find an existing meeting or create a new one.
    
    This is the main entry point for connectors to use. It handles:
    1. Looking for an existing meeting that matches
    2. Creating a new meeting if no match found
    3. Updating the existing meeting with new data if found
    
    Args:
        organization_id: Organization UUID
        scheduled_start: Meeting start time (required for matching)
        participants: List of participant dicts with email, name, etc.
        title: Meeting title
        scheduled_end: Meeting end time
        duration_minutes: Meeting duration
        organizer_email: Email of the meeting organizer
        summary: Meeting summary (from transcript)
        action_items: Action items (from transcript)
        key_topics: Key topics/keywords
        status: Meeting status (scheduled, completed, cancelled)
    
    Returns:
        The found or created Meeting
    """
    if isinstance(organization_id, str):
        organization_id = UUID(organization_id)
    
    # Normalize datetimes to naive UTC for database compatibility
    if scheduled_start.tzinfo is not None:
        scheduled_start = scheduled_start.replace(tzinfo=None)
    if scheduled_end is not None and scheduled_end.tzinfo is not None:
        scheduled_end = scheduled_end.replace(tzinfo=None)
    
    async with get_session(organization_id=str(organization_id)) as session:
        # Try to find existing meeting
        meeting = await find_matching_meeting(
            session=session,
            organization_id=organization_id,
            scheduled_start=scheduled_start,
            participants=participants,
            title=title,
        )
        
        if meeting:
            # Update existing meeting with new data
            logger.info("Found existing meeting %s, updating with new data", meeting.id)
            
            # Merge participants
            if participants:
                meeting.participants = merge_participants(meeting.participants, participants)
                meeting.participant_count = len(meeting.participants)
            
            # Update title if we have one and existing is empty
            if title and not meeting.title:
                meeting.title = title
            
            # Update duration/end time if we have better data
            if duration_minutes and not meeting.duration_minutes:
                meeting.duration_minutes = duration_minutes
            if scheduled_end and not meeting.scheduled_end:
                meeting.scheduled_end = scheduled_end
            
            # Update organizer if we have one
            if organizer_email and not meeting.organizer_email:
                meeting.organizer_email = organizer_email
            
            # Update content fields (transcript data takes precedence)
            if summary:
                meeting.summary = summary
            if action_items:
                meeting.action_items = action_items
            if key_topics:
                meeting.key_topics = key_topics
            
            # Update status if more specific
            if status == "completed" and meeting.status == "scheduled":
                meeting.status = status
            
            await session.commit()
            await session.refresh(meeting)
            return meeting
        
        # Create new meeting
        logger.info("Creating new meeting for '%s' at %s", title, scheduled_start)
        
        meeting = Meeting(
            id=uuid4(),
            organization_id=organization_id,
            title=title,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            duration_minutes=duration_minutes,
            participants=participants,
            organizer_email=organizer_email,
            participant_count=len(participants) if participants else None,
            status=status,
            summary=summary,
            action_items=action_items,
            key_topics=key_topics,
        )
        
        session.add(meeting)
        await session.commit()
        await session.refresh(meeting)
        
        logger.info("Created new meeting %s", meeting.id)
        return meeting


async def link_activity_to_meeting(
    activity_id: str | UUID,
    meeting_id: str | UUID,
    organization_id: str | UUID | None = None,
) -> bool:
    """
    Link an activity to a meeting.
    
    Args:
        activity_id: Activity UUID to link
        meeting_id: Meeting UUID to link to
        organization_id: Organization UUID for RLS context
    
    Returns:
        True if successful, False otherwise
    """
    if isinstance(activity_id, str):
        activity_id = UUID(activity_id)
    if isinstance(meeting_id, str):
        meeting_id = UUID(meeting_id)
    
    org_id_str = str(organization_id) if organization_id else None
    try:
        async with get_session(organization_id=org_id_str) as session:
            activity = await session.get(Activity, activity_id)
            if not activity:
                logger.warning("Activity %s not found", activity_id)
                return False
            
            activity.meeting_id = meeting_id
            await session.commit()
            
            logger.debug("Linked activity %s to meeting %s", activity_id, meeting_id)
            return True
    except Exception as e:
        logger.error("Failed to link activity to meeting: %s", e)
        return False


async def get_meeting_with_activities(
    meeting_id: str | UUID,
    organization_id: str | UUID | None = None,
) -> dict[str, Any] | None:
    """
    Get a meeting with all its linked activities.
    
    Returns a dict with meeting data and a list of linked activities
    (calendar events, transcripts, notes, etc.)
    """
    if isinstance(meeting_id, str):
        meeting_id = UUID(meeting_id)
    
    org_id_str = str(organization_id) if organization_id else None
    async with get_session(organization_id=org_id_str) as session:
        meeting = await session.get(Meeting, meeting_id)
        if not meeting:
            return None
        
        # Get linked activities
        result = await session.execute(
            select(Activity).where(Activity.meeting_id == meeting_id)
        )
        activities = result.scalars().all()
        
        return {
            "meeting": meeting.to_dict(),
            "activities": [
                {
                    "id": str(a.id),
                    "type": a.type,
                    "source_system": a.source_system,
                    "subject": a.subject,
                    "activity_date": f"{a.activity_date.isoformat()}Z" if a.activity_date else None,
                }
                for a in activities
            ],
            "activity_count": len(activities),
        }
