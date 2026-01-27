"""
Zoom connector implementation.

Responsibilities:
- Authenticate with Zoom using OAuth token
- Fetch cloud recordings and meeting transcripts
- Normalize Zoom transcript data to activity records
- Handle pagination and transcript cleanup
"""

import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session

ZOOM_API_BASE = "https://api.zoom.us/v2"
TRANSCRIPT_FILE_TYPES = {"TRANSCRIPT", "CC"}
TRANSCRIPT_FILE_EXTENSIONS = {"vtt", "txt"}
MAX_TRANSCRIPT_LENGTH = 10000

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
        url = f"{ZOOM_API_BASE}{endpoint}"

        logger.debug("Zoom API request: %s %s params=%s", method, url, params)

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

    async def get_recordings(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Get cloud recordings for the authenticated user."""
        recordings: list[dict[str, Any]] = []
        next_page_token: Optional[str] = None

        logger.info(
            "Fetching Zoom recordings from %s to %s",
            start_date.date(),
            end_date.date(),
        )

        while True:
            params: dict[str, Any] = {
                "from": start_date.strftime("%Y-%m-%d"),
                "to": end_date.strftime("%Y-%m-%d"),
                "page_size": 300,
                "trash": False,
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            data = await self._make_request("GET", "/users/me/recordings", params=params)
            meetings = data.get("meetings", [])
            recordings.extend(meetings)
            logger.debug(
                "Zoom recordings page fetched: %s meetings (total=%s)",
                len(meetings),
                len(recordings),
            )

            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break

        logger.info("Fetched %s Zoom meetings with recordings", len(recordings))
        return recordings

    async def _download_transcript(self, download_url: str) -> Optional[str]:
        """Download transcript text using the OAuth token."""
        token, _ = await self.get_oauth_token()

        if not download_url:
            return None

        url = download_url
        if "access_token=" not in url:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}access_token={token}"

        logger.debug("Downloading Zoom transcript from %s", url)

        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0, follow_redirects=True)
            if response.status_code >= 400:
                logger.warning(
                    "Failed to download Zoom transcript (status=%s)",
                    response.status_code,
                )
                return None

            transcript_text = response.text.strip()
            if not transcript_text:
                return None

            return self._clean_transcript(transcript_text)

    def _clean_transcript(self, transcript_text: str) -> str:
        """Clean VTT/SRT transcript text into readable plain text."""
        lines = transcript_text.splitlines()
        cleaned_lines: list[str] = []
        timestamp_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s+-->\s+")

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.upper() == "WEBVTT":
                continue
            if timestamp_pattern.match(stripped):
                continue
            if stripped.isdigit():
                continue
            cleaned_lines.append(stripped)

        return " ".join(cleaned_lines)

    def _parse_zoom_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def _is_transcript_file(self, recording_file: dict[str, Any]) -> bool:
        file_type = (recording_file.get("file_type") or "").upper()
        file_extension = (recording_file.get("file_extension") or "").lower()
        return file_type in TRANSCRIPT_FILE_TYPES or file_extension in TRANSCRIPT_FILE_EXTENSIONS

    def _normalize_transcript(
        self,
        meeting: dict[str, Any],
        recording_file: dict[str, Any],
        transcript_text: str,
    ) -> Activity:
        """Transform Zoom transcript to our Activity model."""
        meeting_id = str(meeting.get("id", ""))
        meeting_uuid = meeting.get("uuid")
        topic = meeting.get("topic") or "Zoom Meeting"

        activity_date = self._parse_zoom_datetime(
            recording_file.get("recording_start")
        ) or self._parse_zoom_datetime(meeting.get("start_time"))

        transcript_length = len(transcript_text)
        truncated_text = transcript_text[:MAX_TRANSCRIPT_LENGTH]

        source_id = f"{meeting_id}:{recording_file.get('id', '')}"

        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=source_id,
            type="zoom_transcript",
            subject=topic,
            description=truncated_text,
            activity_date=activity_date,
            custom_fields={
                "meeting_id": meeting_id,
                "meeting_uuid": meeting_uuid,
                "host_id": meeting.get("host_id"),
                "topic": topic,
                "duration_minutes": meeting.get("duration"),
                "recording_file_id": recording_file.get("id"),
                "recording_start": recording_file.get("recording_start"),
                "recording_end": recording_file.get("recording_end"),
                "file_type": recording_file.get("file_type"),
                "file_extension": recording_file.get("file_extension"),
                "transcript_length": transcript_length,
                "transcript_truncated": transcript_length > MAX_TRANSCRIPT_LENGTH,
            },
        )

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
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)

        recordings = await self.get_recordings(start_date=start_date, end_date=end_date)
        if not recordings:
            logger.info("No Zoom recordings found for transcript sync")
            return 0

        count = 0
        async with get_session() as session:
            for meeting in recordings:
                recording_files = meeting.get("recording_files", [])
                transcript_files = [
                    recording_file
                    for recording_file in recording_files
                    if self._is_transcript_file(recording_file)
                ]

                if not transcript_files:
                    logger.debug(
                        "No transcript files for meeting %s",
                        meeting.get("id"),
                    )
                    continue

                logger.info(
                    "Processing %s transcript file(s) for meeting %s",
                    len(transcript_files),
                    meeting.get("id"),
                )

                for recording_file in transcript_files:
                    transcript_text = await self._download_transcript(
                        recording_file.get("download_url", "")
                    )
                    if not transcript_text:
                        logger.warning(
                            "Transcript download failed for meeting %s file %s",
                            meeting.get("id"),
                            recording_file.get("id"),
                        )
                        continue

                    activity = self._normalize_transcript(
                        meeting, recording_file, transcript_text
                    )
                    await session.merge(activity)
                    count += 1

            await session.commit()

        logger.info("Zoom transcript sync complete: %s activity records", count)
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
