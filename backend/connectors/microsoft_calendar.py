"""
Microsoft Calendar connector implementation via Microsoft Graph API.

Responsibilities:
- Authenticate with Microsoft using OAuth token (via Nango)
- Fetch calendar events and meetings from Outlook
- Normalize Microsoft Calendar data to activity records
- Link events to canonical Meeting entities
- Handle pagination
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session
from services.meeting_dedup import find_or_create_meeting

logger = logging.getLogger(__name__)

MICROSOFT_GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


class MicrosoftCalendarConnector(BaseConnector):
    """Connector for Microsoft Outlook Calendar data via Microsoft Graph."""

    source_system = "microsoft_calendar"

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Microsoft Graph API."""
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Microsoft Graph API."""
        headers = await self._get_headers()
        url = f"{MICROSOFT_GRAPH_API_BASE}{endpoint}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def get_calendars(self) -> list[dict[str, Any]]:
        """Get list of calendars the user has access to."""
        calendars: list[dict[str, Any]] = []
        next_link: Optional[str] = None

        while True:
            if next_link:
                # For pagination, use the full URL
                async with httpx.AsyncClient() as client:
                    headers = await self._get_headers()
                    response = await client.get(next_link, headers=headers, timeout=30.0)
                    response.raise_for_status()
                    data = response.json()
            else:
                data = await self._make_request("GET", "/me/calendars")

            calendars.extend(data.get("value", []))

            next_link = data.get("@odata.nextLink")
            if not next_link:
                break

        return calendars

    async def get_events(
        self,
        calendar_id: Optional[str] = None,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: int = 250,
    ) -> list[dict[str, Any]]:
        """Get events from a specific calendar or the default calendar."""
        if time_min is None:
            time_min = datetime.utcnow() - timedelta(days=30)
        if time_max is None:
            time_max = datetime.utcnow() + timedelta(days=30)

        events: list[dict[str, Any]] = []
        next_link: Optional[str] = None

        # Build endpoint - use default calendar if no calendar_id provided
        if calendar_id:
            endpoint = f"/me/calendars/{calendar_id}/events"
        else:
            endpoint = "/me/calendar/events"

        while len(events) < max_results:
            if next_link:
                # For pagination, use the full URL
                async with httpx.AsyncClient() as client:
                    headers = await self._get_headers()
                    response = await client.get(next_link, headers=headers, timeout=30.0)
                    response.raise_for_status()
                    data = response.json()
            else:
                params: dict[str, Any] = {
                    "$top": min(50, max_results - len(events)),
                    "$orderby": "start/dateTime",
                    "$filter": (
                        f"start/dateTime ge '{time_min.isoformat()}Z' and "
                        f"start/dateTime le '{time_max.isoformat()}Z'"
                    ),
                    "$select": (
                        "id,subject,bodyPreview,start,end,location,attendees,"
                        "organizer,isOnlineMeeting,onlineMeeting,recurrence,"
                        "isCancelled,showAs,importance"
                    ),
                }
                data = await self._make_request("GET", endpoint, params=params)

            events.extend(data.get("value", []))

            next_link = data.get("@odata.nextLink")
            if not next_link:
                break

        return events

    async def sync_deals(self) -> int:
        """Microsoft Calendar doesn't have deals - return 0."""
        return 0

    async def sync_accounts(self) -> int:
        """Microsoft Calendar doesn't have accounts - return 0."""
        return 0

    async def sync_contacts(self) -> int:
        """Microsoft Calendar doesn't have contacts - return 0."""
        return 0

    async def sync_activities(self) -> int:
        """
        Sync Microsoft Calendar events as activities.

        For each event:
        1. Find or create the canonical Meeting entity
        2. Create an Activity record for the calendar event
        3. Link the Activity to the Meeting
        """
        # Get events from default calendar for the last 30 days and next 30 days
        time_min = datetime.utcnow() - timedelta(days=30)
        time_max = datetime.utcnow() + timedelta(days=30)

        events = await self.get_events(
            time_min=time_min,
            time_max=time_max,
            max_results=500,
        )

        count = 0
        async with get_session(organization_id=self.organization_id) as session:
            for event in events:
                try:
                    parsed = self._parse_event(event)
                    if not parsed:
                        continue
                    
                    # Find or create the canonical Meeting
                    meeting = await find_or_create_meeting(
                        organization_id=self.organization_id,
                        scheduled_start=parsed["activity_date"],
                        scheduled_end=parsed["end_time"],
                        participants=parsed["participants_normalized"],
                        title=parsed["subject"],
                        duration_minutes=parsed["duration_minutes"],
                        organizer_email=parsed["organizer_email"],
                        status=parsed["meeting_status"],
                    )
                    
                    # Create the Activity record linked to the Meeting
                    activity = Activity(
                        id=uuid.uuid4(),
                        organization_id=uuid.UUID(self.organization_id),
                        source_system=self.source_system,
                        source_id=parsed["event_id"],
                        meeting_id=meeting.id,
                        type=parsed["meeting_type"],
                        subject=parsed["subject"] or "Untitled Event",
                        description=parsed["body_preview"],
                        activity_date=parsed["activity_date"],
                        custom_fields={
                            "organizer_email": parsed["organizer_email"],
                            "location": parsed["location"],
                            "attendee_count": parsed["attendee_count"],
                            "attendee_emails": parsed["attendee_emails"],
                            "duration_minutes": parsed["duration_minutes"],
                            "is_recurring": parsed["is_recurring"],
                            "conference_link": parsed["conference_link"],
                            "show_as": parsed["show_as"],
                            "importance": parsed["importance"],
                        },
                    )
                    
                    await session.merge(activity)
                    count += 1
                    
                    logger.debug(
                        "Synced calendar event %s linked to meeting %s",
                        parsed["event_id"],
                        meeting.id,
                    )
                    
                except Exception as e:
                    logger.error("Error syncing calendar event: %s", e)
                    continue

            await session.commit()

        return count

    def _parse_event(self, ms_event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Parse Microsoft Calendar event into normalized fields."""
        event_id: str = ms_event.get("id", "")
        subject: str = ms_event.get("subject", "")
        body_preview: str = ms_event.get("bodyPreview", "")

        # Skip cancelled events
        if ms_event.get("isCancelled"):
            return None

        # Parse start time
        start = ms_event.get("start", {})
        activity_date: Optional[datetime] = None

        if start.get("dateTime"):
            try:
                dt_str: str = start["dateTime"]
                activity_date = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        if not activity_date:
            return None

        # Parse end time for duration calculation
        end = ms_event.get("end", {})
        end_time: Optional[datetime] = None

        if end.get("dateTime"):
            try:
                dt_str = end["dateTime"]
                end_time = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        # Calculate duration in minutes
        duration_minutes: Optional[int] = None
        if activity_date and end_time:
            duration = end_time - activity_date
            duration_minutes = int(duration.total_seconds() / 60)

        # Extract attendees
        attendees_raw: list[dict[str, Any]] = ms_event.get("attendees", [])
        attendee_emails: list[str] = []
        participants_normalized: list[dict[str, Any]] = []
        
        for attendee in attendees_raw:
            email_address = attendee.get("emailAddress", {})
            email = email_address.get("address", "")
            name = email_address.get("name", "")
            if email:
                attendee_emails.append(email)
                participants_normalized.append({
                    "email": email.lower(),
                    "name": name,
                    "is_organizer": False,
                    "rsvp_status": attendee.get("status", {}).get("response", ""),
                })
        
        attendee_count = len(attendees_raw)

        # Get organizer
        organizer = ms_event.get("organizer", {})
        organizer_email_obj = organizer.get("emailAddress", {})
        organizer_email: Optional[str] = organizer_email_obj.get("address")

        # Mark organizer in participants
        if organizer_email:
            for p in participants_normalized:
                if p["email"] == organizer_email.lower():
                    p["is_organizer"] = True
                    break

        # Determine meeting type
        meeting_type = "meeting"
        conference_link: Optional[str] = None
        if ms_event.get("isOnlineMeeting"):
            online_meeting = ms_event.get("onlineMeeting", {})
            conference_link = online_meeting.get("joinUrl", "")
            if "teams" in conference_link.lower():
                meeting_type = "teams_meeting"
            elif "zoom" in conference_link.lower():
                meeting_type = "zoom"
            else:
                meeting_type = "online_meeting"

        # Determine meeting status
        if activity_date < datetime.utcnow():
            meeting_status = "completed"
        else:
            meeting_status = "scheduled"

        # Get location
        location = ms_event.get("location", {})
        location_display: Optional[str] = location.get("displayName") if location else None

        return {
            "event_id": event_id,
            "subject": subject,
            "body_preview": body_preview[:2000] if body_preview else None,
            "activity_date": activity_date,
            "end_time": end_time,
            "duration_minutes": duration_minutes,
            "attendee_emails": attendee_emails[:10],
            "attendee_count": attendee_count,
            "participants_normalized": participants_normalized,
            "organizer_email": organizer_email,
            "meeting_type": meeting_type,
            "meeting_status": meeting_status,
            "is_recurring": ms_event.get("recurrence") is not None,
            "location": location_display,
            "conference_link": conference_link,
            "show_as": ms_event.get("showAs"),
            "importance": ms_event.get("importance"),
        }

    async def sync_all(self) -> dict[str, int]:
        """Run all sync operations."""
        activities_count = await self.sync_activities()

        return {
            "accounts": 0,
            "deals": 0,
            "contacts": 0,
            "activities": activities_count,
        }

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Microsoft Calendar doesn't have deals."""
        return {"error": "Microsoft Calendar does not support deals"}

    async def get_free_busy(
        self,
        time_min: datetime,
        time_max: datetime,
        schedules: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Get free/busy information for users.
        
        Args:
            time_min: Start of time range
            time_max: End of time range  
            schedules: List of email addresses to check (defaults to current user)
        """
        # This would require POST request with body
        # Placeholder for future implementation
        return {}

    async def create_event(
        self,
        subject: str,
        start_time: datetime,
        end_time: datetime,
        body: Optional[str] = None,
        attendees: Optional[list[str]] = None,
        is_online_meeting: bool = False,
    ) -> dict[str, Any]:
        """Create a new calendar event."""
        # This would require POST capability
        # Placeholder for future implementation
        raise NotImplementedError("Event creation not implemented in MVP")
