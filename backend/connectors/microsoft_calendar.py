"""
Microsoft Calendar connector implementation via Microsoft Graph API.

Responsibilities:
- Authenticate with Microsoft using OAuth token (via Nango)
- Fetch calendar events and meetings from Outlook
- Normalize Microsoft Calendar data to activity records
- Handle pagination
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session

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

        This captures meeting activity that can be correlated
        with deals and accounts.
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
        async with get_session() as session:
            for event in events:
                activity = self._normalize_event(event)
                if activity:
                    await session.merge(activity)
                    count += 1

            await session.commit()

        return count

    def _normalize_event(self, ms_event: dict[str, Any]) -> Optional[Activity]:
        """Transform Microsoft Calendar event to our Activity model."""
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
                # Microsoft returns ISO format datetime
                dt_str: str = start["dateTime"]
                # Handle timezone - Microsoft returns UTC for events with timeZone
                activity_date = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

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
        attendees: list[dict[str, Any]] = ms_event.get("attendees", [])
        attendee_emails: list[str] = []
        for attendee in attendees:
            email_address = attendee.get("emailAddress", {})
            email = email_address.get("address")
            if email:
                attendee_emails.append(email)
        attendee_count = len(attendees)

        # Determine meeting type
        meeting_type = "meeting"
        if ms_event.get("isOnlineMeeting"):
            online_meeting = ms_event.get("onlineMeeting", {})
            join_url = online_meeting.get("joinUrl", "")
            if "teams" in join_url.lower():
                meeting_type = "teams_meeting"
            elif "zoom" in join_url.lower():
                meeting_type = "zoom"
            else:
                meeting_type = "online_meeting"

        # Check if it's a recurring event
        is_recurring = ms_event.get("recurrence") is not None

        # Get location
        location = ms_event.get("location", {})
        location_display: Optional[str] = location.get("displayName") if location else None

        # Get organizer email
        organizer = ms_event.get("organizer", {})
        organizer_email_obj = organizer.get("emailAddress", {})
        organizer_email: Optional[str] = organizer_email_obj.get("address")

        # Get online meeting link
        conference_link: Optional[str] = None
        if ms_event.get("isOnlineMeeting"):
            online_meeting = ms_event.get("onlineMeeting", {})
            conference_link = online_meeting.get("joinUrl")

        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=event_id,
            type=meeting_type,
            subject=subject or "Untitled Event",
            description=body_preview[:2000] if body_preview else None,
            activity_date=activity_date,
            custom_fields={
                "organizer_email": organizer_email,
                "location": location_display,
                "attendee_count": attendee_count,
                "attendee_emails": attendee_emails[:10],  # Limit stored attendees
                "duration_minutes": duration_minutes,
                "is_recurring": is_recurring,
                "conference_link": conference_link,
                "show_as": ms_event.get("showAs"),
                "importance": ms_event.get("importance"),
            },
        )

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
