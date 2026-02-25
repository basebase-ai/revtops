"""
Granola connector – sync meeting notes and support on-demand note queries.

Granola is a note-taking and meeting management platform. This connector:
- SYNC: pulls notes as activities (type=note) for the warehouse
- QUERY: list notes and get a single note (with optional transcript)

Auth: API key stored in Nango. Configure Granola in Nango (API key).
API docs: https://docs.granola.ai/api-reference
Base URL: https://public-api.granola.ai
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from connectors.base import BaseConnector
from connectors.models import ActivityRecord
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorMeta,
    ConnectorScope,
)

logger = logging.getLogger(__name__)

GRANOLA_API_BASE: str = "https://public-api.granola.ai"
GRANOLA_PAGE_SIZE: int = 30  # API max
GRANOLA_SYNC_MAX_PAGES: int = 20  # Cap sync to avoid runaway


class GranolaConnector(BaseConnector):
    """Connector for Granola – meeting notes and transcripts."""

    source_system: str = "granola"
    meta = ConnectorMeta(
        name="Granola",
        slug="granola",
        auth_type=AuthType.API_KEY,
        scope=ConnectorScope.USER,
        entity_types=["notes"],
        capabilities=[Capability.SYNC, Capability.QUERY],
        nango_integration_id="granola",
        description="Granola – meeting notes and transcripts",
        query_description="List notes (optional: created_after, cursor) or get a note by id (e.g. not_xxx). Use get_schema for fields.",
    )

    async def _get_headers(self) -> dict[str, str]:
        token: str
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _list_notes(
        self,
        page_size: int = GRANOLA_PAGE_SIZE,
        cursor: str | None = None,
        created_after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": page_size}
        if cursor:
            params["cursor"] = cursor
        if created_after:
            params["created_after"] = created_after

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.get(
                f"{GRANOLA_API_BASE}/v1/notes",
                headers=await self._get_headers(),
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def _get_note(self, note_id: str, include_transcript: bool = False) -> dict[str, Any]:
        params: dict[str, str] = {}
        if include_transcript:
            params["include"] = "transcript"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.get(
                f"{GRANOLA_API_BASE}/v1/notes/{note_id}",
                headers=await self._get_headers(),
                params=params if params else None,
            )
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # CRM abstract methods (not applicable – return 0 / empty)
    # ------------------------------------------------------------------

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> list[ActivityRecord]:
        """Sync Granola notes as activities (type=note)."""
        await self.ensure_sync_active("sync_activities:start")
        records: list[ActivityRecord] = []
        cursor: str | None = None
        page_count: int = 0

        while page_count < GRANOLA_SYNC_MAX_PAGES:
            data: dict[str, Any] = await self._list_notes(cursor=cursor)
            notes: list[dict[str, Any]] = data.get("notes") or []
            for n in notes:
                note_id: str = n.get("id") or ""
                title: str | None = n.get("title")
                created_at_raw: str | None = n.get("created_at")
                activity_date: datetime | None = None
                if created_at_raw:
                    try:
                        activity_date = datetime.fromisoformat(
                            created_at_raw.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass
                records.append(
                    ActivityRecord(
                        source_id=note_id,
                        type="note",
                        subject=title or "(No title)",
                        description=None,
                        activity_date=activity_date,
                        source_system=self.source_system,
                    )
                )
            has_more: bool = data.get("hasMore", False)
            cursor = data.get("cursor") if isinstance(data.get("cursor"), str) else None
            page_count += 1
            if not has_more or not cursor:
                break
            await self.ensure_sync_active("sync_activities:page")

        return records

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        raise NotImplementedError("Granola connector does not support deals")

    # ------------------------------------------------------------------
    # QUERY capability
    # ------------------------------------------------------------------

    async def get_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "entity": "notes",
                "fields": [
                    "id",
                    "title",
                    "owner (name, email)",
                    "created_at",
                    "updated_at",
                    "summary_text",
                    "summary_markdown",
                    "calendar_event",
                    "attendees",
                ],
                "description": "List notes via query with optional created_after (ISO date), cursor. Get one note by id (e.g. not_1d3tmYTlCICgjy), optional include=transcript.",
            },
        ]

    async def query(self, request: str) -> dict[str, Any]:
        """Execute an on-demand query: list notes or get note by id."""
        req = (request or "").strip().lower()
        # Get single note by id (e.g. "not_1d3tmYTlCICgjy" or "get not_xxx")
        if req.startswith("not_") and " " not in req:
            note = await self._get_note(request.strip(), include_transcript=False)
            return {"results": [note], "query": request}
        if req.startswith("get ") and "not_" in req:
            parts = request.strip().split()
            note_id = next((p for p in parts if p.startswith("not_")), None)
            if note_id:
                include_transcript = "transcript" in req
                note = await self._get_note(note_id, include_transcript=include_transcript)
                return {"results": [note], "query": request}

        # List notes (optional: created_after=..., cursor=...)
        params: dict[str, Any] = {}
        for part in request.split():
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip().lower()] = v.strip()
        created_after = params.get("created_after")
        cursor = params.get("cursor")
        try:
            page_size_val = min(GRANOLA_PAGE_SIZE, int(params["page_size"])) if "page_size" in params else GRANOLA_PAGE_SIZE
        except (ValueError, TypeError):
            page_size_val = GRANOLA_PAGE_SIZE
        data = await self._list_notes(
            page_size=page_size_val,
            cursor=cursor,
            created_after=created_after,
        )
        return {"results": data.get("notes", []), "hasMore": data.get("hasMore"), "cursor": data.get("cursor"), "query": request}
