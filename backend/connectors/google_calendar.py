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
from services.meeting_dedup import find_or_create_meeting

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_MEET_API_BASE = "https://meet.googleapis.com/v2"


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
                description="Create an instant Google Meet huddle. Returns a Meet link and meeting code to share with participants.",
                parameters=[
                    {"name": "title", "type": "string", "required": False, "description": "Meeting title (default: 'Huddle')"},
                    {"name": "duration_minutes", "type": "integer", "required": False, "description": "Duration in minutes (default: 30, metadata only)"},
                ],
            ),
            ConnectorAction(
                name="end_huddle",
                description="End an active huddle's conference.",
                parameters=[
                    {"name": "meeting_id", "type": "string", "required": True, "description": "UUID of the Meeting entity"},
                ],
            ),
        ],
        usage_guide=(
            "Use create_huddle to start an instant Google Meet meeting — it returns "
            "a meet_link and meeting_code. Share the link with participants directly. "
            "Use end_huddle to wrap up. Recordings must be started manually in Meet; "
            "they are auto-fetched via Meet API after the huddle ends."
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

    async def _make_meet_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Google Meet REST API v2."""
        headers = await self._get_headers()
        url = f"{GOOGLE_MEET_API_BASE}{endpoint}"

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
            if response.status_code == 204:
                return {}
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

        time_min: datetime = self.sync_since or (datetime.utcnow() - timedelta(days=30))
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
                        # Re-attach to outer session so meeting_code etc. persist
                        meeting = await session.merge(meeting)

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
                    
                    # Set Meet conference fields on the meeting if available
                    if meeting and parsed.get("conference_id") and parsed["meeting_type"] == "google_meet":
                        if not meeting.meeting_code:
                            meeting.meeting_code = parsed["conference_id"]
                        if not meeting.conference_link and parsed.get("conference_link"):
                            meeting.conference_link = parsed["conference_link"]
                        if not meeting.google_event_id:
                            meeting.google_event_id = parsed["event_id"]
                        if not meeting.organizer_email and parsed.get("organizer_email"):
                            meeting.organizer_email = parsed["organizer_email"]
                        # If Gemini attached notes directly, fetch the summary now
                        gemini_doc_id = parsed.get("gemini_doc_id", "")
                        if gemini_doc_id and not meeting.summary:
                            try:
                                summary = await self._fetch_gemini_doc(gemini_doc_id)
                                if summary:
                                    meeting.summary = summary
                                    logger.info(
                                        "[GCal Sync] Saved Gemini summary (%d chars) from attachment for meeting %s",
                                        len(summary), meeting.id,
                                    )
                            except Exception as e:
                                logger.warning("[GCal Sync] Failed to fetch Gemini doc %s: %s", gemini_doc_id, e)

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
            # Wrapped in try/except: concurrent user syncs can race and link
            # activities between our check and the delete
            try:
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
            except Exception as e:
                logger.warning("[GCal Sync] Orphan cleanup failed (likely race condition): %s", e)

        # Schedule Gemini summary fetch for completed Google Meet meetings
        # that have a meeting_code but no summary yet
        try:
            from models.meeting import Meeting as MeetingModel
            async with get_session(organization_id=self.organization_id) as session:
                # Only check meetings from the last 7 days — older ones are unlikely
                # to still have Gemini summaries we haven't fetched
                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
                needs_summary = await session.execute(
                    select(MeetingModel).where(
                        MeetingModel.organization_id == uuid.UUID(self.organization_id),
                        MeetingModel.status == "completed",
                        MeetingModel.meeting_code.isnot(None),
                        MeetingModel.summary.is_(None),
                        MeetingModel.huddle_status.is_(None),  # Skip huddles — handled by sweep
                        MeetingModel.scheduled_start > cutoff,
                    )
                )
                meetings_needing_summary = needs_summary.scalars().all()

            if meetings_needing_summary:
                from workers.tasks.sync import check_huddle_recording
                for m in meetings_needing_summary:
                    check_huddle_recording.apply_async(
                        args=[str(m.id), self.organization_id],
                        countdown=10,
                    )
                logger.info(
                    "[GCal Sync] Scheduled summary fetch for %d completed Meet meetings",
                    len(meetings_needing_summary),
                )
        except Exception as e:
            logger.warning("[GCal Sync] Failed to schedule summary fetches: %s", e)

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

        # Determine meeting type and extract conference details
        meeting_type = "meeting"
        conference_id = ""  # Meet meeting code (e.g. "aaa-bbbb-ccc")
        if gcal_event.get("conferenceData"):
            conf_data = gcal_event["conferenceData"]
            conf_type = conf_data.get("conferenceSolution", {}).get("name", "")
            if "meet" in conf_type.lower():
                meeting_type = "google_meet"
                conference_id = conf_data.get("conferenceId", "")
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
            "conference_id": conference_id,
            "gemini_doc_id": self._extract_gemini_doc_id(gcal_event),
            "visibility": gcal_event.get("visibility"),
        }

    async def _fetch_gemini_doc(self, doc_id: str) -> str:
        """Export a Gemini notes doc as plain text using the Drive API."""
        from workers.tasks.sync import _get_google_token

        # Look up user email for organizer-scoped token
        organizer_email = None
        if self.user_id:
            from models.database import get_admin_session
            from models.user import User

            async with get_admin_session() as session:
                user = await session.get(User, uuid.UUID(self.user_id))
                if user:
                    organizer_email = user.email

        token = await _get_google_token(
            None, self.organization_id, organizer_email, preferred_connector="google_drive"
        )
        if not token:
            return ""

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{doc_id}/export",
                headers={"Authorization": f"Bearer {token}"},
                params={"mimeType": "text/plain"},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.text.strip()

    @staticmethod
    def _extract_gemini_doc_id(gcal_event: dict[str, Any]) -> str:
        """Extract the Gemini notes doc fileId from calendar event attachments."""
        attachments = gcal_event.get("attachments", [])
        for att in attachments:
            if (
                att.get("mimeType") == "application/vnd.google-apps.document"
                and "gemini" in att.get("title", "").lower()
            ):
                return att.get("fileId", "")
        if attachments:
            logger.debug(
                "[GCal Sync] %d attachment(s) on event %s but none matched Gemini: %s",
                len(attachments),
                gcal_event.get("id", "?"),
                [att.get("title", "") for att in attachments],
            )
        return ""

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
        if action == "end_huddle":
            return await self._action_end_huddle(params)
        raise ValueError(f"Unknown action: {action}")

    async def _action_create_huddle(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create an instant Google Meet huddle via Meet REST API v2."""
        title = params.get("title", "Huddle")
        duration_minutes: int = params.get("duration_minutes", 30)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        end = now + timedelta(minutes=duration_minutes)

        # Create a Meet space (empty body → server picks meeting code)
        try:
            data = await self._make_meet_request("POST", "/spaces", json_body={})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                error_body = exc.response.text
                logger.error("Meet API 403: %s", error_body)
                return {
                    "status": "error",
                    "error": f"Meet space creation denied (403): {error_body}",
                }
            raise

        meet_space_name = data.get("name", "")          # "spaces/abc123"
        meeting_uri = data.get("meetingUri", "")         # "https://meet.google.com/abc-mnop-xyz"
        meeting_code = data.get("meetingCode", "")       # "abc-mnop-xyz"

        # Look up organizer email from the user whose token created the space
        organizer_email = None
        if self.user_id:
            from models.user import User
            async with get_session(organization_id=self.organization_id) as session:
                user = await session.get(User, uuid.UUID(self.user_id))
                if user:
                    organizer_email = user.email

        # Create canonical Meeting entity
        meeting = await find_or_create_meeting(
            organization_id=self.organization_id,
            scheduled_start=now,
            scheduled_end=end,
            participants=[],
            title=title,
            duration_minutes=duration_minutes,
            organizer_email=organizer_email,
            status="scheduled",
        )

        # Re-fetch in a new session to set Meet-specific fields and create Activity
        from models.meeting import Meeting
        async with get_session(organization_id=self.organization_id) as session:
            m = await session.get(Meeting, meeting.id)
            m.conference_link = meeting_uri
            m.meet_space_name = meet_space_name
            m.meeting_code = meeting_code
            m.huddle_status = "active"

            # Create an Activity so the huddle is discoverable via activity search
            vis = self._activity_visibility_fields()
            activity = Activity(
                id=uuid.uuid4(),
                organization_id=uuid.UUID(self.organization_id),
                source_system=self.source_system,
                source_id=f"huddle-{meeting_code}",
                meeting_id=meeting.id,
                type="google_meet",
                subject=title,
                activity_date=now,
                **vis,
                custom_fields={
                    "conference_link": meeting_uri,
                    "meeting_code": meeting_code,
                    "is_huddle": True,
                },
            )
            session.add(activity)
            await session.commit()
            meeting_id = str(m.id)

        return {
            "status": "ok",
            "meeting_id": meeting_id,
            "meet_link": meeting_uri,
            "meeting_code": meeting_code,
            "title": title,
        }

    async def _action_end_huddle(self, params: dict[str, Any]) -> dict[str, Any]:
        """End an active huddle via Meet API (or Calendar fallback for legacy huddles)."""
        from models.meeting import Meeting

        meeting_id = params.get("meeting_id")
        if not meeting_id:
            raise ValueError("end_huddle requires meeting_id")

        async with get_session(organization_id=self.organization_id) as session:
            meeting = await session.get(Meeting, uuid.UUID(meeting_id))
            if not meeting:
                raise ValueError(f"Meeting {meeting_id} not found")

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            if meeting.meet_space_name:
                # ── New path: Meet REST API v2 ──
                space = meeting.meet_space_name  # "spaces/abc123"
                try:
                    await self._make_meet_request(
                        "POST", f"/{space}:endActiveConference", json_body={}
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        # Conference already ended or never started
                        pass
                    elif exc.response.status_code == 403:
                        return {
                            "status": "error",
                            "error": "Meet API access denied. Please re-authorize the integration.",
                        }
                    else:
                        raise

            elif meeting.google_event_id:
                # ── Legacy fallback: Calendar PATCH ──
                google_event_id = meeting.google_event_id
                try:
                    await self._make_request(
                        "PATCH",
                        f"/calendars/primary/events/{google_event_id}",
                        json_body={
                            "end": {"dateTime": now.isoformat() + "Z", "timeZone": "UTC"},
                        },
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        pass  # event already deleted
                    elif exc.response.status_code == 403:
                        return {
                            "status": "error",
                            "error": "Calendar write access denied. Please re-authorize with full calendar scope.",
                        }
                    else:
                        raise
            else:
                raise ValueError("Meeting has no linked Meet space or Google Calendar event")

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
