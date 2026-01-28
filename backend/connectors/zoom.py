"""
Zoom connector implementation.

Responsibilities:
- Authenticate with Zoom via OAuth token
- Fetch cloud recordings and transcripts
- Normalize transcript data into Activity records
- Handle pagination and rate limits
"""

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session
from services.meeting_dedup import find_or_create_meeting

ZOOM_API_BASE = "https://api.zoom.us/v2"
TRANSCRIPT_FILE_TYPES = {"TRANSCRIPT", "VTT"}
MAX_TRANSCRIPT_LENGTH = 4000

logger = logging.getLogger(__name__)


class ZoomConnector(BaseConnector):
    """Connector for Zoom meeting transcripts."""

    source_system = "zoom"

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Zoom API."""
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
        """Make an authenticated request to Zoom API."""
        headers = await self._get_headers()
        url = f"{ZOOM_API_BASE}/{endpoint}"
        logger.debug("Zoom API request", extra={"method": method, "url": url, "params": params})

        async with httpx.AsyncClient() as client:
            if method == "GET":
                response = await client.get(url, headers=headers, params=params, timeout=30.0)
            else:
                response = await client.post(url, headers=headers, json=params, timeout=30.0)

            response.raise_for_status()
            return response.json()

    async def _download_transcript(
        self,
        client: httpx.AsyncClient,
        download_url: str,
        token: str,
    ) -> str:
        """Download transcript content from Zoom."""
        url = httpx.URL(download_url).copy_add_param("access_token", token)
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        return response.text

    def _normalize_transcript_text(self, raw_text: str) -> str:
        """Normalize transcript content by stripping WebVTT metadata."""
        cleaned_lines: list[str] = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.upper() == "WEBVTT":
                continue
            if "-->" in stripped:
                continue
            if stripped.isdigit():
                continue
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines).strip()

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    async def _fetch_recordings(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Fetch Zoom cloud recordings within a date range."""
        meetings: list[dict[str, Any]] = []
        next_page_token: Optional[str] = None

        logger.info(
            "Fetching Zoom recordings",
            extra={"from": start_date.isoformat(), "to": end_date.isoformat()},
        )

        while True:
            params: dict[str, Any] = {
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
                "page_size": 300,
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            data = await self._make_request("GET", "users/me/recordings", params=params)
            page_meetings = data.get("meetings", [])
            meetings.extend(page_meetings)

            logger.debug(
                "Fetched Zoom recordings page",
                extra={"page_count": len(page_meetings), "total": len(meetings)},
            )

            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break

        return meetings

    async def sync_deals(self) -> int:
        """Zoom doesn't have deals - return 0."""
        return 0

    async def sync_accounts(self) -> int:
        """Zoom doesn't have accounts - return 0."""
        return 0

    async def sync_contacts(self) -> int:
        """Zoom doesn't have contacts - return 0."""
        return 0

    async def sync_activities(self) -> int:
        """Sync Zoom meeting transcripts as activities."""
        now = datetime.utcnow()
        start_date = (now - timedelta(days=7)).date()
        end_date = now.date()

        meetings = await self._fetch_recordings(start_date, end_date)
        logger.info("Processing Zoom recordings", extra={"count": len(meetings)})

        count = 0
        token, _ = await self.get_oauth_token()

        async with httpx.AsyncClient() as client:
            async with get_session() as session:
                for meeting in meetings:
                    meeting_id = meeting.get("id")
                    meeting_uuid = meeting.get("uuid")
                    topic = meeting.get("topic") or "Zoom Meeting"
                    start_time = self._parse_datetime(meeting.get("start_time"))
                    duration_minutes = meeting.get("duration")
                    host_email = meeting.get("host_email") or meeting.get("host_id")

                    recording_files = meeting.get("recording_files", [])
                    transcript_files = [
                        f for f in recording_files
                        if f.get("file_type") in TRANSCRIPT_FILE_TYPES
                    ]

                    if not transcript_files:
                        logger.debug(
                            "No transcript files for Zoom meeting",
                            extra={"meeting_id": meeting_id, "meeting_uuid": meeting_uuid},
                        )
                        continue
                    if not start_time:
                        logger.warning(
                            "Zoom meeting missing start_time; skipping",
                            extra={"meeting_id": meeting_id, "meeting_uuid": meeting_uuid},
                        )
                        continue

                    participants: list[dict[str, Any]] = []
                    if host_email:
                        participants.append(
                            {
                                "email": host_email,
                                "name": host_email.split("@")[0]
                                if isinstance(host_email, str) and "@" in host_email
                                else str(host_email),
                                "is_organizer": True,
                            }
                        )

                    meeting_record = await find_or_create_meeting(
                        organization_id=self.organization_id,
                        scheduled_start=start_time,
                        scheduled_end=(
                            start_time + timedelta(minutes=duration_minutes)
                            if isinstance(duration_minutes, int)
                            else None
                        ),
                        duration_minutes=duration_minutes if isinstance(duration_minutes, int) else None,
                        participants=participants or None,
                        organizer_email=host_email if isinstance(host_email, str) else None,
                        title=topic,
                        summary=None,
                        action_items=None,
                        key_topics=None,
                        status="completed",
                    )
                    logger.info(
                        "Matched Zoom recording to meeting",
                        extra={"meeting_id": meeting_record.id, "zoom_meeting_id": meeting_id},
                    )

                    for transcript_file in transcript_files:
                        download_url = transcript_file.get("download_url")
                        if not download_url:
                            logger.warning(
                                "Missing download URL for transcript",
                                extra={"meeting_id": meeting_id, "file_id": transcript_file.get("id")},
                            )
                            continue

                        try:
                            raw_text = await self._download_transcript(client, download_url, token)
                            transcript_text = self._normalize_transcript_text(raw_text)
                        except Exception as exc:
                            logger.exception(
                                "Failed to download Zoom transcript",
                                extra={"meeting_id": meeting_id, "error": str(exc)},
                            )
                            continue

                        if not transcript_text:
                            logger.debug(
                                "Empty transcript after normalization",
                                extra={"meeting_id": meeting_id},
                            )
                            continue

                        source_id = f"{meeting_uuid}:{transcript_file.get('id') or transcript_file.get('file_id')}"
                        activity = Activity(
                            id=uuid.uuid4(),
                            organization_id=uuid.UUID(self.organization_id),
                            source_system=self.source_system,
                            source_id=source_id,
                            meeting_id=meeting_record.id,
                            type="zoom_transcript",
                            subject=topic,
                            description=transcript_text[:MAX_TRANSCRIPT_LENGTH],
                            activity_date=start_time,
                            custom_fields={
                                "meeting_id": meeting_id,
                                "meeting_uuid": meeting_uuid,
                                "recording_id": transcript_file.get("recording_id"),
                                "file_id": transcript_file.get("id"),
                                "file_type": transcript_file.get("file_type"),
                                "file_size": transcript_file.get("file_size"),
                                "duration_minutes": meeting.get("duration"),
                                "host_id": meeting.get("host_id"),
                                "start_time": meeting.get("start_time"),
                                "transcript_length": len(transcript_text),
                            },
                        )

                        await session.merge(activity)
                        count += 1

                await session.commit()

        logger.info("Zoom transcript sync complete", extra={"activities": count})
        return count

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
        """Zoom doesn't have deals."""
        return {"error": "Zoom does not support deals"}
