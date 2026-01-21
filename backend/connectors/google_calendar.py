"""
Google Calendar connector implementation.

Responsibilities:
- Authenticate with Google using OAuth token
- Fetch calendar events and meetings
- Normalize Google Calendar data to activity records
- Handle pagination
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarConnector(BaseConnector):
    """Connector for Google Calendar data."""

    source_system = "google_calendar"

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Google Calendar API."""
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
        """Make an authenticated request to Google Calendar API."""
        headers = await self._get_headers()
        url = f"{GOOGLE_CALENDAR_API_BASE}{endpoint}"

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
        page_token: Optional[str] = None

        while True:
            params: dict[str, Any] = {"maxResults": 250}
            if page_token:
                params["pageToken"] = page_token

            data = await self._make_request("GET", "/users/me/calendarList", params=params)
            calendars.extend(data.get("items", []))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return calendars

    async def get_events(
        self,
        calendar_id: str = "primary",
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: int = 250,
    ) -> list[dict[str, Any]]:
        """Get events from a specific calendar."""
        if time_min is None:
            time_min = datetime.utcnow() - timedelta(days=30)
        if time_max is None:
            time_max = datetime.utcnow() + timedelta(days=30)

        events: list[dict[str, Any]] = []
        page_token: Optional[str] = None

        while len(events) < max_results:
            params: dict[str, Any] = {
                "maxResults": min(250, max_results - len(events)),
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": time_min.isoformat() + "Z",
                "timeMax": time_max.isoformat() + "Z",
            }
            if page_token:
                params["pageToken"] = page_token

            # URL encode calendar ID
            encoded_calendar_id = calendar_id.replace("@", "%40")
            data = await self._make_request(
                "GET", f"/calendars/{encoded_calendar_id}/events", params=params
            )
            events.extend(data.get("items", []))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return events

    async def sync_deals(self) -> int:
        """Google Calendar doesn't have deals - return 0."""
        return 0

    async def sync_accounts(self) -> int:
        """Google Calendar doesn't have accounts - return 0."""
        return 0

    async def sync_contacts(self) -> int:
        """Google Calendar doesn't have contacts - return 0."""
        return 0

    async def sync_activities(self) -> int:
        """
        Sync Google Calendar events as activities.

        This captures meeting activity that can be correlated
        with deals and accounts.
        """
        # Get events from primary calendar for the last 30 days and next 30 days
        time_min = datetime.utcnow() - timedelta(days=30)
        time_max = datetime.utcnow() + timedelta(days=30)

        events = await self.get_events(
            calendar_id="primary",
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

    def _normalize_event(self, gcal_event: dict[str, Any]) -> Optional[Activity]:
        """Transform Google Calendar event to our Activity model."""
        event_id = gcal_event.get("id", "")
        summary = gcal_event.get("summary", "")
        description = gcal_event.get("description", "")

        # Skip cancelled events
        if gcal_event.get("status") == "cancelled":
            return None

        # Parse start time
        start = gcal_event.get("start", {})
        activity_date: Optional[datetime] = None

        if start.get("dateTime"):
            try:
                # Handle timezone-aware datetime
                dt_str = start["dateTime"]
                activity_date = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        elif start.get("date"):
            try:
                # All-day event
                activity_date = datetime.strptime(start["date"], "%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        # Parse end time for duration calculation
        end = gcal_event.get("end", {})
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
        attendees = gcal_event.get("attendees", [])
        attendee_emails = [a.get("email") for a in attendees if a.get("email")]
        attendee_count = len(attendees)

        # Determine meeting type
        meeting_type = "meeting"
        if gcal_event.get("conferenceData"):
            conf_type = gcal_event.get("conferenceData", {}).get("conferenceSolution", {}).get("name", "")
            if "meet" in conf_type.lower():
                meeting_type = "google_meet"
            elif "zoom" in conf_type.lower():
                meeting_type = "zoom"

        # Check if it's a recurring event
        is_recurring = "recurringEventId" in gcal_event

        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=event_id,
            type=meeting_type,
            subject=summary or "Untitled Event",
            description=description[:2000] if description else None,
            activity_date=activity_date,
            custom_fields={
                "calendar_id": gcal_event.get("organizer", {}).get("email"),
                "location": gcal_event.get("location"),
                "attendee_count": attendee_count,
                "attendee_emails": attendee_emails[:10],  # Limit stored attendees
                "duration_minutes": duration_minutes,
                "is_recurring": is_recurring,
                "conference_link": gcal_event.get("hangoutLink"),
                "status": gcal_event.get("status"),
                "visibility": gcal_event.get("visibility"),
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
        """Google Calendar doesn't have deals."""
        return {"error": "Google Calendar does not support deals"}

    async def get_free_busy(
        self,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
    ) -> dict[str, list[dict[str, str]]]:
        """Get free/busy information for calendars."""
        # Note: This would be a POST request with body
        # Simplified for MVP
        return {}

    async def create_event(
        self,
        summary: str,
        start_time: datetime,
        end_time: datetime,
        description: Optional[str] = None,
        attendees: Optional[list[str]] = None,
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Create a new calendar event."""
        # This would require POST capability
        # Placeholder for future implementation
        raise NotImplementedError("Event creation not implemented in MVP")
