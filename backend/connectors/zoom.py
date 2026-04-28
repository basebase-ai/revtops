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
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm.exc import DetachedInstanceError

from connectors.base import BaseConnector
from connectors.registry import AuthType, Capability, ConnectorMeta, ConnectorScope
from models.activity import Activity
from models.database import get_admin_session
from services.meeting_dedup import find_or_create_meeting

ZOOM_API_BASE = "https://api.zoom.us/v2"
TRANSCRIPT_FILE_TYPES = {"TRANSCRIPT", "VTT"}
MAX_TRANSCRIPT_LENGTH = 4000

logger = logging.getLogger(__name__)


def _safe_meeting_id(meeting_record: Any) -> uuid.UUID:
    """Extract meeting UUID without triggering lazy loads on detached ORM rows."""
    try:
        state = sa_inspect(meeting_record)
        identity = getattr(state, "identity", None)
        if identity and identity[0] is not None:
            return uuid.UUID(str(identity[0]))
    except NoInspectionAvailable:
        pass

    raw_id = getattr(getattr(meeting_record, "__dict__", {}), "get", lambda _k: None)("id")
    if raw_id is not None:
        return uuid.UUID(str(raw_id))

    try:
        return uuid.UUID(str(meeting_record.id))
    except DetachedInstanceError as exc:
        raise ValueError("Meeting record id is unavailable on detached SQLAlchemy instance") from exc


class ZoomConnector(BaseConnector):
    """Connector for Zoom meeting transcripts."""

    source_system = "zoom"
    meta = ConnectorMeta(
        name="Zoom",
        slug="zoom",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.USER,
        entity_types=["activities"],
        capabilities=[Capability.SYNC],
        nango_integration_id="zoom",
        description="Zoom – meeting transcript sync",
    )

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
        await self.ensure_sync_active("sync_activities:start")
        now: datetime = datetime.utcnow()
        start_date: date = self.sync_since.date() if self.sync_since else (now - timedelta(days=7)).date()
        end_date: date = now.date()

        meetings = await self._fetch_recordings(start_date, end_date)
        logger.info("Processing Zoom recordings", extra={"count": len(meetings)})

        count: int = 0
        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)
        token, _ = await self.get_oauth_token()

        async with httpx.AsyncClient() as client:
            # Bypass RLS so we see activities written by other users' integrations.
            # Otherwise owner_only rows from teammate syncs are invisible and we hit
            # uq_activities_org_source on INSERT for the same Zoom transcript id.
            async with get_admin_session() as session:
                from sqlalchemy import select

                for meeting in meetings:
                    meeting_id: Any = meeting.get("id")
                    meeting_uuid: Any = meeting.get("uuid")
                    topic: str = meeting.get("topic") or "Zoom Meeting"
                    start_time: datetime | None = self._parse_datetime(meeting.get("start_time"))
                    duration_minutes: Any = meeting.get("duration")
                    host_email: Any = meeting.get("host_email") or meeting.get("host_id")

                    recording_files: list[dict[str, Any]] = meeting.get("recording_files", [])
                    transcript_files: list[dict[str, Any]] = [
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
                        status="completed",
                    )
                    meeting_record_id: uuid.UUID = _safe_meeting_id(meeting_record)
                    logger.info(
                        "Matched Zoom recording to meeting",
                        extra={"meeting_id": meeting_record_id, "zoom_meeting_id": meeting_id},
                    )

                    for transcript_file in transcript_files:
                        download_url: str | None = transcript_file.get("download_url")
                        if not download_url:
                            logger.warning(
                                "Missing download URL for transcript",
                                extra={"meeting_id": meeting_id, "file_id": transcript_file.get("id")},
                            )
                            continue

                        try:
                            raw_text: str = await self._download_transcript(client, download_url, token)
                            transcript_text: str = self._normalize_transcript_text(raw_text)
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

                        source_id: str = f"{meeting_uuid}:{transcript_file.get('id') or transcript_file.get('file_id')}"

                        try:
                            async with session.begin_nested():
                                existing_result = await session.execute(
                                    select(Activity).where(
                                        Activity.organization_id == org_uuid,
                                        Activity.source_system == self.source_system,
                                        Activity.source_id == source_id,
                                    )
                                )
                                activity: Activity | None = existing_result.scalar_one_or_none()

                                if activity is None:
                                    vis: dict[str, Any] = self._activity_visibility_fields()
                                    activity = Activity(
                                        id=uuid.uuid4(),
                                        organization_id=org_uuid,
                                        source_system=self.source_system,
                                        source_id=source_id,
                                        **vis,
                                    )
                                    session.add(activity)

                                activity.meeting_id = meeting_record_id
                                activity.type = "zoom_transcript"
                                activity.subject = topic
                                activity.description = transcript_text[:MAX_TRANSCRIPT_LENGTH]
                                activity.activity_date = start_time
                                activity.custom_fields = {
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
                                }
                                activity.synced_at = datetime.utcnow()
                                count += 1
                        except Exception as exc:
                            logger.exception(
                                "Error upserting Zoom transcript activity",
                                extra={"meeting_id": meeting_id, "source_id": source_id},
                            )
                            continue

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
