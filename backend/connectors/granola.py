"""
Granola MCP connector implementation.

Connects to the Granola MCP server (https://mcp.granola.ai/mcp) to sync
meeting notes and transcripts as Activity records.

Uses raw Streamable HTTP transport (JSON-RPC over HTTP POST) — no MCP SDK
dependency required.  OAuth tokens are managed by Nango, same as Fireflies/Zoom.

Granola MCP tools used:
  - list_meetings: meeting IDs, titles, dates, attendees
  - get_meetings:  full meeting content (enhanced notes, private notes)
  - get_meeting_transcript: raw transcript (paid Granola tiers only)
"""

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy import select

from api.websockets import broadcast_sync_progress
from connectors.base import BaseConnector
from connectors.registry import AuthType, Capability, ConnectorMeta, ConnectorScope
from models.activity import Activity
from models.database import get_admin_session, get_session
from services.meeting_dedup import find_or_create_meeting

logger = logging.getLogger(__name__)

GRANOLA_MCP_URL = "https://mcp.granola.ai/mcp"
MCP_JSONRPC_VERSION = "2.0"
MAX_DESCRIPTION_LENGTH = 4000


# ---------------------------------------------------------------------------
# Lightweight MCP client (Streamable HTTP transport, JSON-RPC 2.0)
# ---------------------------------------------------------------------------


class GranolaMcpClient:
    """Thin wrapper around the Granola MCP server using Streamable HTTP."""

    def __init__(self, bearer_token: str) -> None:
        self._token: str = bearer_token
        self._request_id: int = 0
        self._session_id: str | None = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a JSON-RPC 2.0 request and return the result."""
        payload: dict[str, Any] = {
            "jsonrpc": MCP_JSONRPC_VERSION,
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        async with httpx.AsyncClient(timeout=60.0) as client:
            response: httpx.Response = await client.post(
                GRANOLA_MCP_URL,
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()

            session_id: str | None = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id

            content_type: str = response.headers.get("content-type", "")

            if "text/event-stream" in content_type:
                return self._parse_sse(response.text)

            data: dict[str, Any] = response.json()
            if "error" in data:
                error_info: dict[str, Any] = data["error"]
                raise RuntimeError(
                    f"MCP error {error_info.get('code')}: {error_info.get('message')}"
                )
            return data.get("result")

    @staticmethod
    def _parse_sse(raw_text: str) -> Any:
        """Extract the last JSON-RPC result from an SSE stream."""
        last_result: Any = None
        for line in raw_text.splitlines():
            if line.startswith("data: "):
                try:
                    event_data: dict[str, Any] = json.loads(line[6:])
                    if "result" in event_data:
                        last_result = event_data["result"]
                    elif "error" in event_data:
                        error_info: dict[str, Any] = event_data["error"]
                        raise RuntimeError(
                            f"MCP error {error_info.get('code')}: {error_info.get('message')}"
                        )
                except json.JSONDecodeError:
                    continue
        return last_result

    async def initialize(self) -> dict[str, Any]:
        """Perform MCP initialize handshake."""
        result: dict[str, Any] = await self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "basebase", "version": "1.0.0"},
        })
        # Send initialized notification (no id, no response expected)
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                GRANOLA_MCP_URL,
                headers=self._headers(),
                json={
                    "jsonrpc": MCP_JSONRPC_VERSION,
                    "method": "notifications/initialized",
                },
            )
        return result or {}

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call an MCP tool and return the result content."""
        params: dict[str, Any] = {
            "name": tool_name,
            "arguments": arguments if arguments is not None else {},
        }
        return await self._rpc("tools/call", params)

    async def list_meetings(self) -> list[dict[str, Any]]:
        """Call list_meetings tool — returns meeting stubs."""
        result: Any = await self.call_tool("list_meetings")
        text: str | None = _extract_text(result)
        if text:
            return _parse_meetings_xml(text)
        return _extract_content_list(result)

    async def get_meetings(
        self,
        *,
        meeting_ids: list[str] | None = None,
        query: str | None = None,
    ) -> str:
        """Call get_meetings tool — returns meeting notes as text."""
        args: dict[str, Any] = {}
        if meeting_ids:
            args["meeting_ids"] = meeting_ids
        if query:
            args["query"] = query
        result: Any = await self.call_tool("get_meetings", args)
        return _extract_text(result) or ""

    async def get_meeting_transcript(self, meeting_id: str) -> str | None:
        """Call get_meeting_transcript — returns raw transcript text or None."""
        try:
            result: Any = await self.call_tool(
                "get_meeting_transcript",
                {"meeting_id": meeting_id},
            )
            return _extract_text(result)
        except RuntimeError as exc:
            if "paid" in str(exc).lower() or "unauthorized" in str(exc).lower():
                logger.debug("Transcript not available (paid tier): %s", exc)
                return None
            raise

    async def query_meetings(self, query: str) -> str:
        """Call query_granola_meetings — semantic search across meetings."""
        result: Any = await self.call_tool(
            "query_granola_meetings",
            {"query": query},
        )
        return _extract_text(result) or ""


def _extract_content_list(result: Any) -> list[dict[str, Any]]:
    """Parse MCP tool result into a list of content dicts.

    MCP tool results are typically:
        {"content": [{"type": "text", "text": "<json>"}]}
    """
    if not result:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        content: Any = result.get("content", [])
        if isinstance(content, list):
            parsed: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text: str = item.get("text", "")
                    try:
                        data: Any = json.loads(text)
                        if isinstance(data, list):
                            parsed.extend(data)
                        elif isinstance(data, dict):
                            parsed.append(data)
                    except json.JSONDecodeError:
                        parsed.append({"raw_text": text})
            return parsed
    return []


def _extract_text(result: Any) -> str | None:
    """Extract plain text from an MCP tool result."""
    if not result:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content: Any = result.get("content", [])
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(parts) if parts else None
    return None


_MEETING_TAG_RE = re.compile(
    r'<meeting\s+id="([^"]+)"\s+title="([^"]+)"\s+date="([^"]+)"',
    re.DOTALL,
)
_PARTICIPANTS_RE = re.compile(
    r"<known_participants>\s*(.*?)\s*</known_participants>",
    re.DOTALL,
)
_EMAIL_RE = re.compile(r"<([^>]+@[^>]+)>")
_PARTICIPANT_ENTRY_RE = re.compile(
    r"([^<,]+?)(?:\s+from\s+[^<,]+?)?\s*<([^>]+@[^>]+)>"
)


def _parse_meetings_xml(text: str) -> list[dict[str, Any]]:
    """Parse the XML-like meeting list returned by Granola's list_meetings tool."""
    meetings: list[dict[str, Any]] = []

    chunks: list[str] = re.split(r"(?=<meeting\s)", text)
    for chunk in chunks:
        tag_match = _MEETING_TAG_RE.search(chunk)
        if not tag_match:
            continue

        meeting_id: str = tag_match.group(1)
        title: str = tag_match.group(2)
        date_str: str = tag_match.group(3)

        participants: list[dict[str, str | bool]] = []
        organizer_email: str | None = None

        participants_match = _PARTICIPANTS_RE.search(chunk)
        if participants_match:
            raw_participants: str = participants_match.group(1)

            for entry_match in _PARTICIPANT_ENTRY_RE.finditer(raw_participants):
                name_part: str = entry_match.group(1).strip()
                email: str = entry_match.group(2).strip()
                is_creator: bool = "(note creator)" in name_part
                clean_name: str = name_part.replace("(note creator)", "").strip()

                if is_creator:
                    organizer_email = email

                participants.append({
                    "email": email,
                    "name": clean_name,
                    "is_organizer": is_creator,
                })

        meetings.append({
            "id": meeting_id,
            "title": title,
            "date": date_str,
            "attendees": participants,
            "organizer_email": organizer_email,
        })

    return meetings


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class GranolaConnector(BaseConnector):
    """Connector for Granola meeting notes via their MCP server."""

    source_system = "granola"
    meta = ConnectorMeta(
        name="Granola",
        slug="granola",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.USER,
        entity_types=["activities"],
        capabilities=[Capability.SYNC, Capability.QUERY],
        nango_integration_id="granola-mcp",
        query_description=(
            "Query Granola meetings on demand. Supported formats:\n"
            "- Natural language search: 'What did we discuss about pricing?'\n"
            "- Get notes for a specific meeting: 'meeting:<granola_meeting_id>'\n"
            "- Get transcript for a specific meeting: 'transcript:<granola_meeting_id>'\n"
            "Natural language queries use Granola's semantic search across all meetings."
        ),
        description="Granola – meeting notes and transcript sync via MCP",
    )

    async def _get_mcp_client(self) -> GranolaMcpClient:
        """Create an authenticated MCP client for Granola."""
        token: str
        token, _ = await self.get_oauth_token()
        client = GranolaMcpClient(bearer_token=token)
        await client.initialize()
        return client

    # -- Unsupported entity types ------------------------------------------------

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {"error": "Granola does not support deals"}

    # -- QUERY capability --------------------------------------------------------

    async def query(self, request: str) -> dict[str, Any]:
        """Execute an on-demand query against Granola meetings.

        Supports:
          - ``meeting:<id>``  — fetch notes for a specific meeting
          - ``transcript:<id>`` — fetch transcript for a specific meeting
          - bare text — semantic search via query_granola_meetings
        """
        stripped: str = request.strip()
        if not stripped:
            return {"error": "Empty query"}

        mcp: GranolaMcpClient = await self._get_mcp_client()
        lower: str = stripped.lower()

        if lower.startswith("meeting:"):
            meeting_id: str = stripped[len("meeting:"):].strip()
            if not meeting_id:
                return {"error": "meeting_id is required after 'meeting:'"}
            notes: str = await mcp.get_meetings(meeting_ids=[meeting_id])
            return {"meeting_id": meeting_id, "notes": notes}

        if lower.startswith("transcript:"):
            meeting_id = stripped[len("transcript:"):].strip()
            if not meeting_id:
                return {"error": "meeting_id is required after 'transcript:'"}
            transcript: str | None = await mcp.get_meeting_transcript(meeting_id)
            if transcript is None:
                return {
                    "meeting_id": meeting_id,
                    "error": "Transcript not available (may require a paid Granola tier)",
                }
            return {"meeting_id": meeting_id, "transcript": transcript}

        result_text: str = await mcp.query_meetings(stripped)
        return {"query": stripped, "results": result_text}

    # -- Main sync ---------------------------------------------------------------

    async def sync_activities(self) -> int:
        """Sync Granola meeting notes as Activity records.

        Flow:
        1. list_meetings → get meeting stubs
        2. get_meetings per meeting → get notes content
        3. Optionally get_meeting_transcript for raw transcript
        4. find_or_create_meeting for dedup
        5. Upsert Activity linked to the Meeting
        """
        await self.ensure_sync_active("sync_activities:start")
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
        )

        mcp: GranolaMcpClient = await self._get_mcp_client()

        logger.info(
            "Fetching Granola meetings for org %s", self.organization_id
        )
        meetings_list: list[dict[str, Any]] = await mcp.list_meetings()
        logger.info("Got %d meetings from Granola", len(meetings_list))

        if self.sync_since:
            filtered: list[dict[str, Any]] = []
            for stub in meetings_list:
                raw_date: Any = stub.get("date") or stub.get("start_time")
                meeting_dt: datetime = _parse_datetime(raw_date)
                if meeting_dt >= self.sync_since:
                    filtered.append(stub)
            logger.info(
                "Granola incremental filter: %d → %d meetings since %s",
                len(meetings_list), len(filtered), self.sync_since,
            )
            meetings_list = filtered

        count: int = 0
        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)

        # Bypass RLS so we see activities written by other users' integrations.
        # Otherwise owner_only rows from teammate syncs are invisible and we hit
        # uq_activities_org_source on INSERT for the same Granola meeting id.
        async with get_admin_session() as session:
            for meeting_stub in meetings_list:
                try:
                    async with session.begin_nested():
                        parsed: dict[str, Any] | None = self._parse_meeting(
                            meeting_stub
                        )
                        if not parsed:
                            continue

                        notes_text: str = ""
                        transcript_text: str | None = None
                        meeting_id: str | None = parsed.get("meeting_id")
                        if meeting_id:
                            notes_text = await mcp.get_meetings(
                                meeting_ids=[meeting_id],
                            )

                            transcript_text = await mcp.get_meeting_transcript(
                                meeting_id
                            )
                            if transcript_text:
                                parsed["has_transcript"] = True
                                if not notes_text:
                                    notes_text = transcript_text[:MAX_DESCRIPTION_LENGTH]

                        meeting = await find_or_create_meeting(
                            organization_id=self.organization_id,
                            scheduled_start=parsed["activity_date"],
                            participants=parsed["participants_normalized"],
                            title=parsed["title"],
                            duration_minutes=parsed.get("duration_minutes"),
                            organizer_email=parsed.get("organizer_email"),
                            notes_source="granola",
                            notes_text=notes_text[:500] if notes_text else None,
                            action_items=parsed.get("action_items_structured"),
                            key_topics=parsed.get("keywords"),
                            status="completed",
                        )

                        if transcript_text and meeting:
                            from models.meeting import Meeting as MeetingModel
                            meeting_row: MeetingModel | None = await session.get(
                                MeetingModel, meeting.id
                            )
                            if meeting_row:
                                meeting_row.transcript = transcript_text

                        source_id: str = parsed["source_id"]

                        existing_result = await session.execute(
                            select(Activity).where(
                                Activity.organization_id == org_uuid,
                                Activity.source_system == self.source_system,
                                Activity.source_id == source_id,
                            )
                        )
                        activity: Activity | None = (
                            existing_result.scalar_one_or_none()
                        )

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

                        description: str = notes_text[:MAX_DESCRIPTION_LENGTH] if notes_text else ""

                        activity.meeting_id = meeting.id
                        activity.type = "meeting_notes"
                        activity.subject = parsed["title"]
                        activity.description = description
                        activity.activity_date = parsed["activity_date"]
                        activity.custom_fields = {
                            "granola_meeting_id": meeting_id,
                            "duration_minutes": parsed.get("duration_minutes"),
                            "participant_count": parsed.get("participant_count", 0),
                            "participants": parsed.get("participants_raw", []),
                            "organizer_email": parsed.get("organizer_email"),
                            "has_transcript": parsed.get("has_transcript", False),
                            "keywords": parsed.get("keywords", []),
                            "has_action_items": parsed.get("has_action_items", False),
                        }
                        activity.synced_at = datetime.utcnow()
                        count += 1

                    await broadcast_sync_progress(
                        organization_id=self.organization_id,
                        provider=self.source_system,
                        count=count,
                        status="syncing",
                    )
                    logger.debug(
                        "Synced Granola meeting %s -> meeting %s",
                        source_id,
                        meeting.id,
                    )

                except Exception as exc:
                    logger.exception("Error syncing Granola meeting")
                    continue

            await session.commit()

        return count

    async def sync_all(self) -> dict[str, int]:
        """Run all sync operations."""
        try:
            activities_count: int = await self.sync_activities()
        except Exception:
            await broadcast_sync_progress(
                organization_id=self.organization_id,
                provider=self.source_system,
                count=0,
                status="failed",
            )
            raise

        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=activities_count,
            status="completed",
        )

        return {
            "accounts": 0,
            "deals": 0,
            "contacts": 0,
            "activities": activities_count,
        }

    # -- Parsing helpers ---------------------------------------------------------

    @staticmethod
    def _parse_meeting(
        meeting_stub: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Parse a Granola list_meetings stub into normalised fields."""
        meeting_id: str | None = (
            meeting_stub.get("id") or meeting_stub.get("meeting_id")
        )
        if not meeting_id:
            return None

        title: str = meeting_stub.get("title", "Untitled Meeting")

        # Parse date — Granola may return ISO-8601 or epoch
        raw_date: Any = meeting_stub.get("date") or meeting_stub.get("start_time")
        activity_date: datetime = _parse_datetime(raw_date)

        duration_minutes: int | None = meeting_stub.get("duration_minutes") or (
            (meeting_stub.get("duration") or 0) // 60
            if meeting_stub.get("duration")
            else None
        )

        attendees_raw: list[Any] = (
            meeting_stub.get("attendees")
            or meeting_stub.get("participants")
            or []
        )
        participants_normalized: list[dict[str, str | bool]] = []
        organizer_email: str | None = meeting_stub.get("organizer_email")

        for att in attendees_raw[:30]:
            if isinstance(att, str):
                is_organizer: bool = att.lower() == (organizer_email or "").lower()
                participants_normalized.append({
                    "email": att,
                    "name": att.split("@")[0] if "@" in att else att,
                    "is_organizer": is_organizer,
                })
            elif isinstance(att, dict):
                email: str = att.get("email", "")
                name: str = (
                    att.get("name", "")
                    or att.get("displayName", "")
                    or (email.split("@")[0] if email and "@" in email else "")
                )
                participants_normalized.append({
                    "email": email,
                    "name": name,
                    "is_organizer": email.lower() == (organizer_email or "").lower(),
                })

        # Action items (may be in stub or in detail)
        action_items: list[Any] = meeting_stub.get("action_items", []) or []
        action_items_structured: list[dict[str, str]] = []
        for item in action_items[:10]:
            if isinstance(item, str):
                action_items_structured.append({"text": item})
            elif isinstance(item, dict):
                action_items_structured.append(item)

        keywords: list[str] = meeting_stub.get("keywords", []) or []

        return {
            "meeting_id": meeting_id,
            "source_id": f"granola:{meeting_id}",
            "title": title,
            "activity_date": activity_date,
            "duration_minutes": duration_minutes,
            "participants_raw": attendees_raw[:30],
            "participants_normalized": participants_normalized,
            "participant_count": len(participants_normalized),
            "organizer_email": organizer_email,
            "keywords": keywords,
            "action_items_structured": action_items_structured,
            "has_action_items": bool(action_items_structured),
            "has_transcript": False,
            "description": "",
        }



_HUMAN_DATE_FORMATS: list[str] = [
    "%b %d, %Y %I:%M %p",   # "Mar 12, 2026 6:00 PM"
    "%B %d, %Y %I:%M %p",   # "March 12, 2026 6:00 PM"
    "%b %d, %Y",             # "Mar 12, 2026"
    "%Y-%m-%dT%H:%M:%S",    # ISO-8601 without tz
    "%Y-%m-%d %H:%M:%S",    # "2026-03-12 18:00:00"
    "%Y-%m-%d",              # "2026-03-12"
]


def _parse_datetime(raw: Any) -> datetime:
    """Parse an ISO-8601 string, human date, epoch seconds, or epoch millis."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        cleaned: str = raw.strip()
        try:
            return datetime.fromisoformat(
                cleaned.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except ValueError:
            pass
        for fmt in _HUMAN_DATE_FORMATS:
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
    if isinstance(raw, (int, float)):
        ts: float = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except (ValueError, OSError):
            pass
    return datetime.utcnow()
