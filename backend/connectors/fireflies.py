"""
Fireflies.ai connector implementation.

Responsibilities:
- Authenticate with Fireflies using OAuth token
- Fetch meeting transcripts and summaries
- Normalize transcript data to activity records
- Link transcripts to canonical Meeting entities
- Handle pagination for transcript lists
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

FIREFLIES_API_BASE = "https://api.fireflies.ai/graphql"


class FirefliesConnector(BaseConnector):
    """Connector for Fireflies.ai meeting transcription data."""

    source_system = "fireflies"

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Fireflies API."""
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _graphql_request(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make a GraphQL request to Fireflies API."""
        headers = await self._get_headers()
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        # Debug: print query being sent
        import json
        print(f"[Fireflies] Sending GraphQL query:")
        print(json.dumps(payload, indent=2))

        async with httpx.AsyncClient() as client:
            response = await client.post(
                FIREFLIES_API_BASE,
                headers=headers,
                json=payload,
                timeout=30.0,
            )
            
            # Log response for debugging
            print(f"[Fireflies] Response status: {response.status_code}")
            print(f"[Fireflies] Response body: {response.text[:1000]}")
            
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            if "errors" in data:
                errors = data["errors"]
                error_msg = errors[0].get("message", "Unknown GraphQL error") if errors else "Unknown error"
                raise ValueError(f"Fireflies API error: {error_msg}")

            return data.get("data", {})

    async def get_transcripts(
        self,
        limit: int = 50,  # Fireflies API max is 50
    ) -> list[dict[str, Any]]:
        """
        Get list of meeting transcripts.

        Args:
            limit: Maximum number of transcripts to fetch

        Returns:
            List of transcript objects
        """
        # Fireflies docs are inconsistent - schema says 'sentence' but examples use 'sentences'
        # Trying minimal query first to debug
        query = """
        query GetTranscripts($limit: Int) {
            transcripts(limit: $limit) {
                id
                title
                date
                duration
                organizer_email
                participants
                meeting_attendees {
                    displayName
                    email
                }
                summary {
                    overview
                    action_items
                    keywords
                }
            }
        }
        """
        variables = {"limit": limit}
        data = await self._graphql_request(query, variables)
        transcripts: list[dict[str, Any]] = data.get("transcripts", [])
        return transcripts

    async def get_transcript(self, transcript_id: str) -> dict[str, Any]:
        """
        Get a single transcript by ID.

        Args:
            transcript_id: The Fireflies transcript ID

        Returns:
            Transcript object with full details
        """
        query = """
        query GetTranscript($id: String!) {
            transcript(id: $id) {
                id
                title
                date
                duration
                organizer_email
                participants
                meeting_attendees {
                    displayName
                    email
                }
                summary {
                    overview
                    action_items
                    keywords
                }
            }
        }
        """
        variables = {"id": transcript_id}
        data = await self._graphql_request(query, variables)
        return data.get("transcript", {})

    async def sync_deals(self) -> int:
        """Fireflies doesn't have deals - return 0."""
        return 0

    async def sync_accounts(self) -> int:
        """Fireflies doesn't have accounts - return 0."""
        return 0

    async def sync_contacts(self) -> int:
        """Fireflies doesn't have contacts - return 0."""
        return 0

    async def sync_activities(self) -> int:
        """
        Sync Fireflies meeting transcripts as activities.

        For each transcript:
        1. Find or create the canonical Meeting entity
        2. Create an Activity record for the transcript
        3. Link the Activity to the Meeting
        
        This ensures transcripts are properly associated with real-world meetings.
        """
        print(f"[Fireflies] Fetching transcripts for org {self.organization_id}")
        transcripts = await self.get_transcripts(limit=50)  # Fireflies max is 50
        print(f"[Fireflies] Got {len(transcripts)} transcripts")

        count = 0
        async with get_session() as session:
            for transcript in transcripts:
                try:
                    # Parse transcript data
                    parsed = self._parse_transcript(transcript)
                    if not parsed:
                        continue
                    
                    # Find or create the canonical Meeting
                    meeting = await find_or_create_meeting(
                        organization_id=self.organization_id,
                        scheduled_start=parsed["activity_date"],
                        participants=parsed["participants_normalized"],
                        title=parsed["title"],
                        duration_minutes=parsed["duration_minutes"],
                        organizer_email=parsed["organizer_email"],
                        summary=parsed["overview"],
                        action_items=parsed["action_items_structured"],
                        key_topics=parsed["keywords"],
                        status="completed",  # Transcripts are for completed meetings
                    )
                    
                    # Create the Activity record linked to the Meeting
                    activity = Activity(
                        id=uuid.uuid4(),
                        organization_id=uuid.UUID(self.organization_id),
                        source_system=self.source_system,
                        source_id=parsed["transcript_id"],
                        meeting_id=meeting.id,
                        type="meeting_transcript",
                        subject=parsed["title"],
                        description=parsed["description"],
                        activity_date=parsed["activity_date"],
                        custom_fields={
                            "duration_minutes": parsed["duration_minutes"],
                            "participant_count": parsed["participant_count"],
                            "participants": parsed["participants_raw"],
                            "organizer_email": parsed["organizer_email"],
                            "keywords": parsed["keywords"],
                            "has_action_items": parsed["has_action_items"],
                        },
                    )
                    
                    await session.merge(activity)
                    count += 1
                    
                    print(f"[Fireflies] Synced transcript {parsed['transcript_id']} -> meeting {meeting.id}")
                    logger.debug(
                        "Synced transcript %s linked to meeting %s",
                        parsed["transcript_id"],
                        meeting.id,
                    )
                    
                except Exception as e:
                    import traceback
                    print(f"[Fireflies] Error syncing transcript: {e}")
                    print(f"[Fireflies] Traceback:\n{traceback.format_exc()}")
                    logger.error("Error syncing transcript: %s", e)
                    continue

            await session.commit()

        return count

    def _parse_transcript(self, transcript: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Parse Fireflies transcript into normalized fields."""
        transcript_id = transcript.get("id")
        if not transcript_id:
            return None

        title = transcript.get("title", "Untitled Meeting")
        date_ms = transcript.get("date")  # Milliseconds since epoch
        duration = transcript.get("duration", 0)
        participants_raw = transcript.get("participants", [])
        summary_data = transcript.get("summary", {}) or {}
        organizer_email = transcript.get("organizer_email")

        # Parse date - Fireflies returns milliseconds since epoch as a float
        activity_date: Optional[datetime] = None
        if date_ms:
            try:
                # Convert milliseconds to seconds for datetime
                timestamp_seconds = float(date_ms) / 1000.0
                activity_date = datetime.utcfromtimestamp(timestamp_seconds)
            except (ValueError, TypeError, OSError):
                activity_date = datetime.utcnow()
        else:
            activity_date = datetime.utcnow()

        # Duration in minutes
        duration_minutes = duration // 60 if duration else 0

        # Parse participants into normalized format
        # Prefer meeting_attendees (has email + displayName) over participants (just emails)
        meeting_attendees = transcript.get("meeting_attendees", []) or []
        participants_normalized: list[dict[str, Any]] = []
        
        if meeting_attendees:
            for att in meeting_attendees[:20]:
                if isinstance(att, dict):
                    email = att.get("email", "")
                    name = att.get("displayName", "") or att.get("name", "")
                    if not name and email:
                        name = email.split("@")[0]
                    participants_normalized.append({
                        "email": email,
                        "name": name,
                        "is_organizer": email == organizer_email,
                    })
        elif isinstance(participants_raw, list):
            for p in participants_raw[:20]:
                if isinstance(p, str):
                    # Fireflies may return emails as strings
                    participants_normalized.append({
                        "email": p,
                        "name": p.split("@")[0] if "@" in p else p,
                        "is_organizer": p == organizer_email,
                    })
                elif isinstance(p, dict):
                    participants_normalized.append({
                        "email": p.get("email", ""),
                        "name": p.get("name", ""),
                        "is_organizer": p.get("email") == organizer_email,
                    })

        # Extract summary fields
        overview = summary_data.get("overview", "")
        action_items_raw = summary_data.get("action_items", [])
        keywords = summary_data.get("keywords", [])

        # Structure action items
        action_items_structured: list[dict[str, Any]] = []
        if action_items_raw:
            for item in action_items_raw[:10]:
                if isinstance(item, str):
                    action_items_structured.append({"text": item})
                elif isinstance(item, dict):
                    action_items_structured.append(item)

        # Build description for Activity
        description_parts: list[str] = []
        if overview:
            description_parts.append(overview)
        if action_items_raw:
            description_parts.append("\n\nAction Items:")
            for item in action_items_raw[:10]:
                item_text = item if isinstance(item, str) else item.get("text", str(item))
                description_parts.append(f"• {item_text}")

        description = "\n".join(description_parts)[:2000]

        return {
            "transcript_id": transcript_id,
            "title": title,
            "activity_date": activity_date,
            "duration_minutes": duration_minutes,
            "participants_raw": participants_raw[:20] if isinstance(participants_raw, list) else [],
            "participants_normalized": participants_normalized,
            "participant_count": len(participants_normalized),
            "organizer_email": organizer_email,
            "overview": overview,
            "keywords": keywords if isinstance(keywords, list) else [],
            "action_items_raw": action_items_raw,
            "action_items_structured": action_items_structured,
            "has_action_items": bool(action_items_raw),
            "description": description,
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
        """Fireflies doesn't have deals."""
        return {"error": "Fireflies does not support deals"}

    async def search_transcripts(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Search for transcripts matching a query.

        Note: Fireflies API may have limited search capabilities.
        This fetches recent transcripts and filters client-side if needed.
        """
        # Fireflies may not have a direct search API, so we fetch and filter
        transcripts = await self.get_transcripts(limit=100)

        query_lower = query.lower()
        matching: list[dict[str, Any]] = []

        for transcript in transcripts:
            title = transcript.get("title", "").lower()
            overview = (transcript.get("summary", {}) or {}).get("overview", "").lower()

            if query_lower in title or query_lower in overview:
                matching.append(transcript)
                if len(matching) >= limit:
                    break

        return matching
