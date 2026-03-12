"""
Google Calendar connector implementation.

Responsibilities:
- Authenticate with Google using OAuth token
- Fetch calendar events and meetings
- Normalize Google Calendar data to activity records
- Link events to canonical Meeting entities
- Handle pagination
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from connectors.registry import AuthType, Capability, ConnectorAction, ConnectorMeta, ConnectorScope
from models.activity import Activity
from models.database import get_session
from services.meeting_dedup import find_or_create_meeting, merge_participants

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarConnector(BaseConnector):
    """Connector for Google Calendar data."""

    source_system = "google_calendar"
    meta = ConnectorMeta(
        name="Google Calendar",
        slug="google_calendar",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.USER,
        entity_types=["activities"],
        capabilities=[Capability.SYNC, Capability.ACTION],
        nango_integration_id="google-calendar",
        description="Google Calendar – event sync and Meet huddle management",
        actions=[
            ConnectorAction(
                name="create_huddle",
                description="Create an instant Google Meet huddle with participants. Returns a Meet link and calendar event.",
                parameters=[
                    {"name": "title", "type": "string", "required": False, "description": "Meeting title (default: 'Huddle')"},
                    {"name": "participants", "type": "array", "required": True, "description": "List of participant email addresses"},
                    {"name": "duration_minutes", "type": "integer", "required": False, "description": "Duration in minutes (default: 30)"},
                    {"name": "description", "type": "string", "required": False, "description": "Meeting description"},
                ],
            ),
            ConnectorAction(
                name="invite_to_huddle",
                description="Add participants to an existing huddle.",
                parameters=[
                    {"name": "meeting_id", "type": "string", "required": True, "description": "UUID of the Meeting entity"},
                    {"name": "participants", "type": "array", "required": True, "description": "Email addresses to add"},
                ],
            ),
            ConnectorAction(
                name="end_huddle",
                description="End an active huddle by shortening the calendar event to now.",
                parameters=[
                    {"name": "meeting_id", "type": "string", "required": True, "description": "UUID of the Meeting entity"},
                ],
            ),
        ],
        usage_guide=(
            "Use create_huddle to start an instant Google Meet meeting. "
            "Use invite_to_huddle to add people to an ongoing huddle. "
            "Use end_huddle to wrap up. Recordings must be started manually in Meet; "
            "they are auto-fetched from Drive after the huddle ends."
        ),
    )

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
        json_body: Optional[dict[str, Any]] = None,
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
                json=json_body,
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

        For each event:
        1. Find or create the canonical Meeting entity
        2. Create an Activity record for the calendar event
        3. Link the Activity to the Meeting
        4. Resolve attendee emails to CRM contact/account/deal FKs
        """
        await self.ensure_sync_active("sync_activities:start")
        from connectors.resolution import build_activity_resolver

        # Get events from primary calendar for the last 30 days and next 30 days
        time_min: datetime = datetime.utcnow() - timedelta(days=30)
        time_max: datetime = datetime.utcnow() + timedelta(days=30)

        # Import broadcast function for real-time progress updates
        from api.websockets import broadcast_sync_progress
        
        print(f"[GCal Sync] Fetching events from {time_min} to {time_max}")
        events: list[dict[str, Any]] = await self.get_events(
            calendar_id="primary",
            time_min=time_min,
            time_max=time_max,
            max_results=500,
        )
        print(f"[GCal Sync] Fetched {len(events)} events from Google Calendar API")
        
        # Broadcast initial progress
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
        )

        # Build resolver from existing CRM data in the database
        resolver = await build_activity_resolver(self.organization_id)

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            from sqlalchemy import select
            
            for event in events:
                try:
                    parsed: Optional[dict[str, Any]] = self._parse_event(event)
                    if not parsed:
                        continue
                    
                    # Convert activity_date to UTC for storage
                    activity_date: Optional[datetime] = parsed["activity_date"]
                    if activity_date and activity_date.tzinfo is not None:
                        activity_date = activity_date.astimezone(timezone.utc).replace(tzinfo=None)

                    # Resolve attendee emails to CRM entities
                    attendee_emails: list[str] = parsed.get("attendee_emails") or []
                    resolved = resolver.resolve(attendee_emails)
                    
                    # Check if we already have an activity for this calendar event
                    # This handles rescheduled meetings - same event ID, different time
                    existing_activity_result = await session.execute(
                        select(Activity).where(
                            Activity.source_system == self.source_system,
                            Activity.source_id == parsed["event_id"],
                            Activity.organization_id == uuid.UUID(self.organization_id),
                        )
                    )
                    existing_activity: Activity | None = existing_activity_result.scalar_one_or_none()
                    
                    if existing_activity and existing_activity.meeting_id:
                        # Event was previously synced - check if time changed (rescheduled)
                        from models.meeting import Meeting
                        existing_meeting = await session.get(Meeting, existing_activity.meeting_id)
                        
                        if existing_meeting and existing_meeting.scheduled_start != activity_date:
                            # Meeting was rescheduled! Update the meeting time
                            print(f"[GCal Sync] Event {parsed['event_id']} rescheduled: {existing_meeting.scheduled_start} -> {activity_date}")
                            existing_meeting.scheduled_start = activity_date
                            if parsed["end_time"]:
                                end_time: datetime = parsed["end_time"]
                                if end_time.tzinfo is not None:
                                    end_time = end_time.astimezone(timezone.utc).replace(tzinfo=None)
                                existing_meeting.scheduled_end = end_time
                            existing_meeting.duration_minutes = parsed["duration_minutes"]
                            existing_meeting.status = parsed["meeting_status"]
                        
                        # Update the activity with latest data + resolved FKs
                        existing_activity.activity_date = activity_date
                        existing_activity.subject = parsed["summary"] or "Untitled Event"
                        existing_activity.description = parsed["description"]
                        existing_activity.contact_id = resolved.contact_id
                        existing_activity.account_id = resolved.account_id
                        existing_activity.deal_id = resolved.deal_id
                        existing_activity.custom_fields = {
                            "calendar_id": parsed["calendar_id"],
                            "location": parsed["location"],
                            "attendee_count": parsed["attendee_count"],
                            "attendee_emails": parsed["attendee_emails"],
                            "duration_minutes": parsed["duration_minutes"],
                            "is_recurring": parsed["is_recurring"],
                            "conference_link": parsed["conference_link"],
                            "status": parsed["event_status"],
                            "visibility": parsed["visibility"],
                        }
                        meeting = existing_meeting
                    else:
                        # New event - find or create canonical Meeting
                        meeting = await find_or_create_meeting(
                            organization_id=self.organization_id,
                            scheduled_start=activity_date,
                            scheduled_end=parsed["end_time"],
                            participants=parsed["participants_normalized"],
                            title=parsed["summary"],
                            duration_minutes=parsed["duration_minutes"],
                            organizer_email=parsed["organizer_email"],
                            status=parsed["meeting_status"],
                        )
                        
                        # Create new Activity record linked to the Meeting
                        vis: dict[str, Any] = self._activity_visibility_fields()
                        activity: Activity = Activity(
                            id=uuid.uuid4(),
                            organization_id=uuid.UUID(self.organization_id),
                            source_system=self.source_system,
                            source_id=parsed["event_id"],
                            meeting_id=meeting.id,
                            contact_id=resolved.contact_id,
                            account_id=resolved.account_id,
                            deal_id=resolved.deal_id,
                            type=parsed["meeting_type"],
                            subject=parsed["summary"] or "Untitled Event",
                            description=parsed["description"],
                            activity_date=activity_date,
                            **vis,
                            custom_fields={
                                "calendar_id": parsed["calendar_id"],
                                "location": parsed["location"],
                                "attendee_count": parsed["attendee_count"],
                                "attendee_emails": parsed["attendee_emails"],
                                "duration_minutes": parsed["duration_minutes"],
                                "is_recurring": parsed["is_recurring"],
                                "conference_link": parsed["conference_link"],
                                "status": parsed["event_status"],
                                "visibility": parsed["visibility"],
                            },
                        )
                        session.add(activity)
                    
                    await session.flush()
                    count += 1
                    
                    # Broadcast progress every 5 events
                    if count % 5 == 0:
                        await broadcast_sync_progress(
                            organization_id=self.organization_id,
                            provider=self.source_system,
                            count=count,
                            status="syncing",
                        )
                    
                    logger.debug(
                        "Synced calendar event %s linked to meeting %s",
                        parsed["event_id"],
                        meeting.id,
                    )
                    
                except Exception as e:
                    import traceback
                    print(f"[GCal Sync] Error syncing event: {e}")
                    print(f"[GCal Sync] Traceback: {traceback.format_exc()}")
                    logger.error("Error syncing calendar event: %s", e)
                    continue

            await session.commit()
            
            # Cleanup orphaned meetings (meetings with no linked activities)
            # These can occur when calendar events are rescheduled
            from models.meeting import Meeting
            from sqlalchemy import func
            
            orphaned_result = await session.execute(
                select(Meeting).where(
                    Meeting.organization_id == uuid.UUID(self.organization_id),
                    Meeting.status == "completed",  # Only cleanup past meetings
                    ~Meeting.id.in_(
                        select(Activity.meeting_id).where(Activity.meeting_id.isnot(None))
                    )
                )
            )
            orphaned_meetings = orphaned_result.scalars().all()
            
            if orphaned_meetings:
                print(f"[GCal Sync] Cleaning up {len(orphaned_meetings)} orphaned meetings")
                for meeting in orphaned_meetings:
                    print(f"[GCal Sync]   Deleting orphaned meeting: {meeting.title} at {meeting.scheduled_start}")
                    await session.delete(meeting)
                await session.commit()

        # Broadcast final progress
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=count,
            status="completed",
        )
        
        print(f"[GCal Sync] Successfully synced {count} activities")
        return count

    def _parse_event(self, gcal_event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Parse Google Calendar event into normalized fields."""
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
                dt_str = start["dateTime"]
                activity_date = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        elif start.get("date"):
            try:
                activity_date = datetime.strptime(start["date"], "%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        if not activity_date:
            return None

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
        attendees_raw = gcal_event.get("attendees", [])
        attendee_emails = [a.get("email") for a in attendees_raw if a.get("email")]
        attendee_count = len(attendees_raw)

        # Parse attendees into normalized format for Meeting
        participants_normalized: list[dict[str, Any]] = []
        for a in attendees_raw:
            email = a.get("email", "")
            if email:
                participants_normalized.append({
                    "email": email.lower(),
                    "name": a.get("displayName", ""),
                    "is_organizer": a.get("organizer", False),
                    "rsvp_status": a.get("responseStatus", ""),
                })

        # Get organizer email
        organizer = gcal_event.get("organizer", {})
        organizer_email = organizer.get("email", "")

        # Determine meeting type
        meeting_type = "meeting"
        if gcal_event.get("conferenceData"):
            conf_type = gcal_event.get("conferenceData", {}).get("conferenceSolution", {}).get("name", "")
            if "meet" in conf_type.lower():
                meeting_type = "google_meet"
            elif "zoom" in conf_type.lower():
                meeting_type = "zoom"

        # Determine meeting status
        event_status = gcal_event.get("status", "confirmed")
        # Convert activity_date to UTC for proper comparison
        now_utc = datetime.now(timezone.utc)
        if activity_date.tzinfo is not None:
            # Compare timezone-aware datetimes directly
            activity_date_utc = activity_date.astimezone(timezone.utc)
        else:
            # Assume naive datetime is already UTC
            activity_date_utc = activity_date.replace(tzinfo=timezone.utc)
        
        if activity_date_utc < now_utc:
            meeting_status = "completed"
        else:
            meeting_status = "scheduled"

        return {
            "event_id": event_id,
            "summary": summary,
            "description": description[:2000] if description else None,
            "activity_date": activity_date,
            "end_time": end_time,
            "duration_minutes": duration_minutes,
            "attendees_raw": attendees_raw,
            "attendee_emails": attendee_emails[:10],
            "attendee_count": attendee_count,
            "participants_normalized": participants_normalized,
            "organizer_email": organizer_email,
            "meeting_type": meeting_type,
            "meeting_status": meeting_status,
            "event_status": event_status,
            "is_recurring": "recurringEventId" in gcal_event,
            "calendar_id": organizer_email,
            "location": gcal_event.get("location"),
            "conference_link": gcal_event.get("hangoutLink"),
            "visibility": gcal_event.get("visibility"),
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

    # ------------------------------------------------------------------
    # ACTION capability – huddle management
    # ------------------------------------------------------------------

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch huddle actions."""
        if action == "create_huddle":
            return await self._action_create_huddle(params)
        if action == "invite_to_huddle":
            return await self._action_invite_to_huddle(params)
        if action == "end_huddle":
            return await self._action_end_huddle(params)
        raise ValueError(f"Unknown action: {action}")

    async def _action_create_huddle(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create an instant Google Meet huddle via Calendar API."""
        title = params.get("title", "Huddle")
        participants: list[str] = params.get("participants") or []
        duration_minutes: int = params.get("duration_minutes", 30)
        description = params.get("description", "")

        if not participants:
            raise ValueError("create_huddle requires at least one participant email")

        now = datetime.utcnow()
        end = now + timedelta(minutes=duration_minutes)

        event_body: dict[str, Any] = {
            "summary": title,
            "description": description,
            "start": {"dateTime": now.isoformat() + "Z", "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat() + "Z", "timeZone": "UTC"},
            "attendees": [{"email": e} for e in participants],
            "conferenceData": {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                },
            },
        }

        try:
            data = await self._make_request(
                "POST",
                "/calendars/primary/events",
                params={"conferenceDataVersion": "1", "sendUpdates": "all"},
                json_body=event_body,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                return {
                    "status": "error",
                    "error": (
                        "Calendar write access denied. The google-calendar integration "
                        "needs the full 'calendar' scope (not calendar.readonly). "
                        "Please re-authorize the integration in Settings → Integrations."
                    ),
                }
            raise

        google_event_id = data.get("id", "")
        meet_link = data.get("hangoutLink", "")

        # If conference creation is still pending, poll once
        conf_data = data.get("conferenceData", {})
        create_status = conf_data.get("createRequest", {}).get("status", {})
        if isinstance(create_status, dict) and create_status.get("statusCode") == "pending":
            import asyncio
            await asyncio.sleep(2)
            data = await self._make_request("GET", f"/calendars/primary/events/{google_event_id}")
            meet_link = data.get("hangoutLink", meet_link)

        # Build normalized participant list
        participants_normalized = [
            {"email": e.lower(), "name": "", "is_organizer": False, "rsvp_status": "needsAction"}
            for e in participants
        ]

        # Create canonical Meeting entity
        async with get_session(organization_id=self.organization_id) as session:
            meeting = await find_or_create_meeting(
                organization_id=self.organization_id,
                scheduled_start=now,
                scheduled_end=end,
                participants=participants_normalized,
                title=title,
                duration_minutes=duration_minutes,
                organizer_email=None,
                status="scheduled",
            )
            meeting.conference_link = meet_link
            meeting.google_event_id = google_event_id
            meeting.huddle_status = "active"
            await session.commit()
            meeting_id = str(meeting.id)

        return {
            "status": "ok",
            "meeting_id": meeting_id,
            "meet_link": meet_link,
            "google_event_id": google_event_id,
            "title": title,
            "participants": participants,
        }

    async def _action_invite_to_huddle(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add participants to an existing huddle."""
        from models.meeting import Meeting

        meeting_id = params.get("meeting_id")
        new_emails: list[str] = params.get("participants") or []

        if not meeting_id:
            raise ValueError("invite_to_huddle requires meeting_id")
        if not new_emails:
            raise ValueError("invite_to_huddle requires at least one participant email")

        async with get_session(organization_id=self.organization_id) as session:
            meeting = await session.get(Meeting, uuid.UUID(meeting_id))
            if not meeting:
                raise ValueError(f"Meeting {meeting_id} not found")
            if not meeting.google_event_id:
                raise ValueError("Meeting has no linked Google Calendar event")

            google_event_id = meeting.google_event_id

            # GET current event to read existing attendees
            event_data = await self._make_request(
                "GET", f"/calendars/primary/events/{google_event_id}"
            )
            existing_attendees = event_data.get("attendees", [])
            existing_emails = {a.get("email", "").lower() for a in existing_attendees}

            # Deduplicate and add new attendees
            added: list[str] = []
            for email in new_emails:
                if email.lower() not in existing_emails:
                    existing_attendees.append({"email": email})
                    added.append(email)

            if added:
                await self._make_request(
                    "PATCH",
                    f"/calendars/primary/events/{google_event_id}",
                    params={"sendUpdates": "all"},
                    json_body={"attendees": existing_attendees},
                )

            # Update Meeting participants
            new_participant_records = [
                {"email": e.lower(), "name": "", "is_organizer": False, "rsvp_status": "needsAction"}
                for e in added
            ]
            meeting.participants = merge_participants(
                meeting.participants or [], new_participant_records
            )
            meeting.participant_count = len(meeting.participants)
            await session.commit()

        return {
            "status": "ok",
            "meeting_id": meeting_id,
            "added_participants": added,
            "total_participants": len(existing_attendees),
        }

    async def _action_end_huddle(self, params: dict[str, Any]) -> dict[str, Any]:
        """End an active huddle by shortening the calendar event."""
        from models.meeting import Meeting

        meeting_id = params.get("meeting_id")
        if not meeting_id:
            raise ValueError("end_huddle requires meeting_id")

        async with get_session(organization_id=self.organization_id) as session:
            meeting = await session.get(Meeting, uuid.UUID(meeting_id))
            if not meeting:
                raise ValueError(f"Meeting {meeting_id} not found")
            if not meeting.google_event_id:
                raise ValueError("Meeting has no linked Google Calendar event")

            google_event_id = meeting.google_event_id
            now = datetime.utcnow()

            # PATCH calendar event end time to now
            try:
                await self._make_request(
                    "PATCH",
                    f"/calendars/primary/events/{google_event_id}",
                    json_body={
                        "end": {"dateTime": now.isoformat() + "Z", "timeZone": "UTC"},
                    },
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 403:
                    return {
                        "status": "error",
                        "error": "Calendar write access denied. Please re-authorize with full calendar scope.",
                    }
                raise

            # Calculate actual duration
            actual_duration = None
            if meeting.scheduled_start:
                delta = now - meeting.scheduled_start
                actual_duration = max(1, int(delta.total_seconds() / 60))

            meeting.status = "completed"
            meeting.huddle_status = "ended"
            meeting.scheduled_end = now
            if actual_duration:
                meeting.duration_minutes = actual_duration
            await session.commit()

        # Schedule recording check with 5-min delay
        try:
            from workers.tasks.sync import check_huddle_recording
            check_huddle_recording.apply_async(
                args=[meeting_id, self.organization_id],
                countdown=300,
            )
        except Exception as e:
            logger.warning("Failed to schedule recording check: %s", e)

        return {
            "status": "ok",
            "meeting_id": meeting_id,
            "actual_duration_minutes": actual_duration,
        }
