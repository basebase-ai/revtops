"""
Linear connector – syncs teams, projects, and issues via the GraphQL API.

Unlike CRM connectors, Linear data doesn't map to accounts/deals/contacts.
The CRM abstract methods are implemented as no-ops; sync_all() is overridden
to run Linear-specific sync operations instead.

OAuth is handled through Nango (Linear OAuth App).
Linear API docs: https://developers.linear.app/docs
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import date, datetime
from typing import Any, Optional, Sequence
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from connectors.base import BaseConnector
from connectors.registry import (
    AuthType, Capability, ConnectorMeta, ConnectorScope, WriteOperation,
)
from models.chat_attachment import ChatAttachment
from models.conversation import Conversation
from models.database import get_session
from models.external_identity_mapping import ExternalIdentityMapping
from models.tracker_issue import TrackerIssue
from models.tracker_project import TrackerProject
from models.tracker_team import TrackerTeam
from models.user import User

logger = logging.getLogger(__name__)

LINEAR_API_URL: str = "https://api.linear.app/graphql"

# Allowed keys for create_issue when unpacking write() data (ignores model hallucinations).
_CREATE_ISSUE_KWARGS: frozenset[str] = frozenset({
    "team_key",
    "title",
    "description",
    "priority",
    "assignee_name",
    "project_name",
    "labels",
    "conversation_id",
    "attachment_ids",
})

_UPDATE_ISSUE_KWARGS: frozenset[str] = frozenset({
    "issue_identifier",
    "title",
    "description",
    "state_name",
    "priority",
    "assignee_name",
    "project_name",
    "conversation_id",
    "attachment_ids",
})

# Event type emitted when an issue is moved to Done (used by webhook route and handle_event)
LINEAR_ISSUE_DONE_EVENT: str = "linear.issue.done"

# ── Priority mapping (Linear uses 0-4) ──────────────────────────────────
PRIORITY_LABELS: dict[int, str] = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}


def _safe_markdown_alt_text(filename: str) -> str:
    cleaned: str = filename.replace("[", "").replace("]", "").replace("\n", " ").strip()
    return (cleaned[:500] if cleaned else "attachment")


def _normalize_uuid_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    items: Sequence[Any] = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        s: str = str(item).strip()
        if s:
            out.append(s)
    return out


class LinearConnector(BaseConnector):
    """Connector for Linear – teams, projects, and issues."""

    source_system: str = "linear"
    meta = ConnectorMeta(
        name="Linear",
        slug="linear",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.USER,
        entity_types=["teams", "projects", "issues"],
        capabilities=[Capability.SYNC, Capability.WRITE, Capability.LISTEN],
        write_operations=[
            WriteOperation(
                name="create_issue", entity_type="issue",
                description="Create a Linear issue",
                parameters=[
                    {"name": "team_key", "type": "string", "required": True, "description": "Team key (e.g. 'ENG')"},
                    {"name": "title", "type": "string", "required": True, "description": "Issue title"},
                    {"name": "description", "type": "string", "required": False, "description": "Issue description (markdown)"},
                    {"name": "priority", "type": "integer", "required": False, "description": "Priority 0-4 (0=none, 1=urgent, 4=low)"},
                    {"name": "assignee_name", "type": "string", "required": False, "description": "Assignee display name"},
                    {"name": "project_name", "type": "string", "required": False, "description": "Project name"},
                    {"name": "labels", "type": "array", "required": False, "description": "Label names to add"},
                    {
                        "name": "attachment_ids",
                        "type": "array",
                        "required": False,
                        "description": "UUIDs of chat_attachments from the user message (see bracketed hint in the user turn). "
                        "Requires conversation_id (injected automatically in chat). Files are uploaded to Linear and linked in the description.",
                    },
                ],
            ),
            WriteOperation(
                name="update_issue", entity_type="issue",
                description="Update an existing Linear issue",
                parameters=[
                    {"name": "issue_identifier", "type": "string", "required": True, "description": "Issue identifier (e.g. 'ENG-123')"},
                    {"name": "title", "type": "string", "required": False, "description": "New title"},
                    {"name": "description", "type": "string", "required": False, "description": "New description"},
                    {"name": "state_name", "type": "string", "required": False, "description": "New state name"},
                    {"name": "priority", "type": "integer", "required": False, "description": "Priority 0-4"},
                    {"name": "assignee_name", "type": "string", "required": False, "description": "Assignee display name"},
                    {"name": "project_name", "type": "string", "required": False, "description": "Project name (move issue into this project)"},
                    {
                        "name": "attachment_ids",
                        "type": "array",
                        "required": False,
                        "description": "UUIDs of chat_attachments; uploads to Linear and appends markdown to the issue description. "
                        "conversation_id is injected in chat. Combine with description to replace body, or omit description to append to the current issue text.",
                    },
                ],
            ),
        ],
        nango_integration_id="linear",
        description="Linear – teams, projects, and issue tracking",
        webhook_secret_extra_data_key="linear_webhook_secret",
        usage_guide="""# Linear Usage Guide

## Write operations (write_on_connector)

Use `write_on_connector(connector='linear', operation='...', data={...})` with `create_issue` or `update_issue`.

### create_issue

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| team_key | string | Yes | Team key (e.g. `ENG`, `DESIGN`). Get from `tracker_teams` table (`key` column, filter `source_system='linear'`). |
| title | string | Yes | Issue title |
| description | string | No | Issue description — Markdown supported |
| priority | integer | No | 0=none, 1=urgent, 2=high, 3=medium, 4=low |
| assignee_name | string | No | Assignee's display name (matched to Linear user) |
| project_name | string | No | Project name (matched to project in the team) |
| labels | array | No | Label names to add |
| attachment_ids | array | No | UUID strings from the `[When creating a Linear issue…]` block in the user message. Uploads each file to Linear and appends markdown links to the issue description. `conversation_id` is injected for you in web/Slack chat — do not type it. |

### update_issue

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| issue_identifier | string | Yes | Issue ID (e.g. `ENG-123`, `DESIGN-45`) |
| title | string | No | New title |
| description | string | No | New description |
| state_name | string | No | New state (e.g. `Done`, `In Progress`) |
| priority | integer | No | 0-4 |
| assignee_name | string | No | New assignee display name |
| project_name | string | No | Project name (matched globally, same as `create_issue`; moves the issue into that project) |
| attachment_ids | array | No | Same as `create_issue`: uploads chat files to Linear and **appends** markdown to the description. If you also pass `description`, that string is used as the base (then attachments are appended). If you omit `description`, the current issue description is fetched and attachments are appended. `conversation_id` is injected in chat. |

### Finding team keys and identifiers

- **Team keys:** `SELECT key, name FROM tracker_teams WHERE source_system = 'linear'`
- **Issue identifiers:** `SELECT identifier, title FROM tracker_issues WHERE source_system = 'linear'`
- **State names:** Vary by team workflow; common: `Backlog`, `Todo`, `In Progress`, `Done`, `Canceled`

### Examples

**Create issue:**
```json
{"operation": "create_issue", "record": {"team_key": "ENG", "title": "Add API rate limiting", "description": "## Context\\nWe need to throttle requests.", "priority": 2}}
```

**Update issue state:**
```json
{"operation": "update_issue", "record": {"issue_identifier": "ENG-123", "state_name": "Done"}}
```

**Querying data:** Use `run_sql_query` on `tracker_teams`, `tracker_projects`, `tracker_issues` (filter `source_system = 'linear'`).
""",
    )

    def __init__(
        self,
        organization_id: str,
        user_id: Optional[str] = None,
        *,
        sync_since_override: datetime | None = None,
    ) -> None:
        super().__init__(
            organization_id, user_id, sync_since_override=sync_since_override
        )

    # ── GraphQL helpers ──────────────────────────────────────────────────

    async def _get_headers(self) -> dict[str, str]:
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"{token}",
            "Content-Type": "application/json",
        }

    async def _gql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query/mutation against the Linear API."""
        headers: dict[str, str] = await self._get_headers()
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.post(
                LINEAR_API_URL,
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()

        if "errors" in result:
            error_msg: str = result["errors"][0].get("message", "Unknown GraphQL error")
            logger.error("Linear GraphQL error: %s", error_msg)
            raise RuntimeError(f"Linear API error: {error_msg}")

        return result.get("data", {})

    async def _gql_paginated(
        self,
        query: str,
        connection_path: list[str],
        variables: dict[str, Any] | None = None,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Paginate through a Linear GraphQL connection (cursor-based).

        Linear connections return:
          { nodes: [...], pageInfo: { hasNextPage, endCursor } }
        """
        all_nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        current_vars: dict[str, Any] = dict(variables or {})

        for _ in range(max_pages):
            if cursor:
                current_vars["after"] = cursor
            data: dict[str, Any] = await self._gql(query, current_vars)

            # Navigate to the connection in the response
            connection: dict[str, Any] = data
            for key in connection_path:
                connection = connection.get(key, {})

            nodes: list[dict[str, Any]] = connection.get("nodes", [])
            if not nodes:
                break
            all_nodes.extend(nodes)

            page_info: dict[str, Any] = connection.get("pageInfo", {})
            if not page_info.get("hasNextPage", False):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break

        return all_nodes

    # ── Integration helper ───────────────────────────────────────────────

    async def _get_integration(self) -> Any:
        """Load the Integration record (cached on self._integration)."""
        if self._integration:
            return self._integration
        await self.get_oauth_token()
        assert self._integration is not None
        return self._integration

    # ── Sync: Teams ──────────────────────────────────────────────────────

    async def sync_teams(self) -> int:
        """Fetch all teams from Linear and upsert into tracker_teams."""
        org_uuid: UUID = UUID(self.organization_id)
        integration = await self._get_integration()
        integration_id: UUID = integration.id

        query: str = """
        query Teams($after: String) {
            teams(first: 100, after: $after) {
                nodes {
                    id
                    name
                    key
                    description
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        teams: list[dict[str, Any]] = await self._gql_paginated(
            query, ["teams"]
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for t in teams:
                stmt = pg_insert(TrackerTeam).values(
                    organization_id=org_uuid,
                    integration_id=integration_id,
                    source_system="linear",
                    source_id=t["id"],
                    name=t["name"],
                    key=t["key"],
                    description=t.get("description"),
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "name": t["name"],
                        "key": t["key"],
                        "description": t.get("description"),
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)
                count += 1
            await session.commit()

        logger.info("Synced %d Linear teams for org %s", count, self.organization_id)
        return count

    # ── Sync: Projects ───────────────────────────────────────────────────

    async def sync_projects(self) -> int:
        """Fetch all projects from Linear and upsert into tracker_projects."""
        org_uuid: UUID = UUID(self.organization_id)

        query: str = """
        query Projects($after: String) {
            projects(first: 50, after: $after) {
                nodes {
                    id
                    name
                    description
                    state
                    progress
                    targetDate
                    startDate
                    url
                    lead {
                        name
                    }
                    teams {
                        nodes {
                            id
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        projects: list[dict[str, Any]] = await self._gql_paginated(
            query, ["projects"]
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for p in projects:
                lead: dict[str, Any] | None = p.get("lead")
                team_nodes: list[dict[str, Any]] = (
                    p.get("teams", {}).get("nodes", [])
                )
                team_ids: list[str] = [tn["id"] for tn in team_nodes]

                stmt = pg_insert(TrackerProject).values(
                    organization_id=org_uuid,
                    source_system="linear",
                    source_id=p["id"],
                    name=p["name"],
                    description=p.get("description"),
                    state=p.get("state"),
                    progress=p.get("progress"),
                    target_date=_parse_date(p.get("targetDate")),
                    start_date=_parse_date(p.get("startDate")),
                    url=p.get("url", ""),
                    lead_name=lead["name"] if lead else None,
                    team_ids=team_ids or None,
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "name": p["name"],
                        "description": p.get("description"),
                        "state": p.get("state"),
                        "progress": p.get("progress"),
                        "target_date": _parse_date(p.get("targetDate")),
                        "start_date": _parse_date(p.get("startDate")),
                        "url": p.get("url", ""),
                        "lead_name": lead["name"] if lead else None,
                        "team_ids": team_ids or None,
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)
                count += 1
            await session.commit()

        logger.info("Synced %d Linear projects for org %s", count, self.organization_id)
        return count

    # ── Sync: Issues ─────────────────────────────────────────────────────

    async def sync_issues(self) -> int:
        """Fetch issues from Linear and upsert into tracker_issues."""
        org_uuid: UUID = UUID(self.organization_id)

        # Build a lookup of source_id → internal UUID for teams
        team_map: dict[str, UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(TrackerTeam.source_id, TrackerTeam.id).where(
                    TrackerTeam.organization_id == org_uuid,
                    TrackerTeam.source_system == "linear",
                )
            )
            for row in result.all():
                team_map[row[0]] = row[1]

        # Build a lookup of source_id → internal UUID for projects
        project_map: dict[str, UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(TrackerProject.source_id, TrackerProject.id).where(
                    TrackerProject.organization_id == org_uuid,
                    TrackerProject.source_system == "linear",
                )
            )
            for row in result.all():
                project_map[row[0]] = row[1]

        query: str = """
        query Issues($after: String) {
            issues(first: 100, after: $after, orderBy: updatedAt) {
                nodes {
                    id
                    identifier
                    title
                    description
                    state {
                        name
                        type
                    }
                    priority
                    priorityLabel
                    assignee {
                        name
                        email
                    }
                    creator {
                        name
                    }
                    project {
                        id
                    }
                    team {
                        id
                    }
                    labels {
                        nodes {
                            name
                        }
                    }
                    estimate
                    url
                    dueDate
                    createdAt
                    updatedAt
                    completedAt
                    canceledAt
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        issues: list[dict[str, Any]] = await self._gql_paginated(
            query, ["issues"]
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for issue in issues:
                if self.sync_since and issue.get("updatedAt"):
                    try:
                        updated_at: datetime = datetime.fromisoformat(
                            issue["updatedAt"].replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        if updated_at < self.sync_since:
                            break
                    except (ValueError, TypeError):
                        pass

                team_data: dict[str, Any] | None = issue.get("team")
                if not team_data:
                    continue  # Skip issues without a team
                linear_team_id: str = team_data["id"]
                internal_team_id: UUID | None = team_map.get(linear_team_id)
                if not internal_team_id:
                    continue  # Team not synced yet

                state_data: dict[str, Any] | None = issue.get("state")
                assignee: dict[str, Any] | None = issue.get("assignee")
                creator: dict[str, Any] | None = issue.get("creator")
                project_data: dict[str, Any] | None = issue.get("project")
                label_nodes: list[dict[str, Any]] = (
                    issue.get("labels", {}).get("nodes", [])
                )
                labels: list[str] = [ln["name"] for ln in label_nodes]

                internal_project_id: UUID | None = None
                if project_data:
                    internal_project_id = project_map.get(project_data["id"])

                stmt = pg_insert(TrackerIssue).values(
                    organization_id=org_uuid,
                    team_id=internal_team_id,
                    source_system="linear",
                    source_id=issue["id"],
                    identifier=issue["identifier"],
                    title=issue["title"],
                    description=issue.get("description"),
                    state_name=state_data["name"] if state_data else None,
                    state_type=state_data["type"] if state_data else None,
                    priority=issue.get("priority"),
                    priority_label=issue.get("priorityLabel"),
                    assignee_name=assignee["name"] if assignee else None,
                    assignee_email=assignee.get("email") if assignee else None,
                    creator_name=creator["name"] if creator else None,
                    project_id=internal_project_id,
                    labels=labels or None,
                    estimate=issue.get("estimate"),
                    url=issue.get("url", ""),
                    due_date=_parse_date(issue.get("dueDate")),
                    created_date=_parse_datetime(issue["createdAt"]),
                    updated_date=_parse_datetime_optional(issue.get("updatedAt")),
                    completed_date=_parse_datetime_optional(issue.get("completedAt")),
                    cancelled_date=_parse_datetime_optional(issue.get("canceledAt")),
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "team_id": internal_team_id,
                        "identifier": issue["identifier"],
                        "title": issue["title"],
                        "description": issue.get("description"),
                        "state_name": state_data["name"] if state_data else None,
                        "state_type": state_data["type"] if state_data else None,
                        "priority": issue.get("priority"),
                        "priority_label": issue.get("priorityLabel"),
                        "assignee_name": assignee["name"] if assignee else None,
                        "assignee_email": assignee.get("email") if assignee else None,
                        "creator_name": creator["name"] if creator else None,
                        "project_id": internal_project_id,
                        "labels": labels or None,
                        "estimate": issue.get("estimate"),
                        "url": issue.get("url", ""),
                        "due_date": _parse_date(issue.get("dueDate")),
                        "updated_date": _parse_datetime_optional(issue.get("updatedAt")),
                        "completed_date": _parse_datetime_optional(issue.get("completedAt")),
                        "cancelled_date": _parse_datetime_optional(issue.get("canceledAt")),
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)
                count += 1
            await session.commit()

        logger.info("Synced %d Linear issues for org %s", count, self.organization_id)
        return count

    async def _upload_bytes_to_linear(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        """Request a signed URL from Linear, PUT file bytes, return asset URL for markdown."""
        mutation: str = """
        mutation LinearFileUpload($contentType: String!, $filename: String!, $size: Int!) {
            fileUpload(contentType: $contentType, filename: $filename, size: $size) {
                success
                uploadFile {
                    uploadUrl
                    assetUrl
                    headers {
                        key
                        value
                    }
                }
            }
        }
        """
        size: int = len(data)
        gql_result: dict[str, Any] = await self._gql(
            mutation,
            {
                "contentType": content_type,
                "filename": filename,
                "size": size,
            },
        )
        payload: dict[str, Any] = gql_result.get("fileUpload", {})
        if not payload.get("success"):
            raise RuntimeError("Linear fileUpload mutation was not successful")
        uf: dict[str, Any] | None = payload.get("uploadFile")
        if not uf or not uf.get("uploadUrl") or not uf.get("assetUrl"):
            raise RuntimeError("Linear fileUpload missing upload URL or asset URL")

        upload_url: str = str(uf["uploadUrl"])
        asset_url: str = str(uf["assetUrl"])
        put_headers: dict[str, str] = {
            "Content-Type": content_type,
            "Cache-Control": "public, max-age=31536000",
        }
        for h in uf.get("headers") or []:
            if isinstance(h, dict) and h.get("key") is not None and h.get("value") is not None:
                put_headers[str(h["key"])] = str(h["value"])

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp: httpx.Response = await client.put(
                upload_url,
                content=data,
                headers=put_headers,
            )
            resp.raise_for_status()

        return asset_url

    async def _load_chat_attachments_for_issue(
        self,
        *,
        conversation_id: str,
        attachment_ids: list[str],
    ) -> list[tuple[str, str, bytes]]:
        """Load attachment bytes scoped to org and conversation; order matches ``attachment_ids``.

        NOTE:
        We intentionally select scalar columns (not ORM instances) so callers can
        safely consume results after the DB session context exits. This avoids
        detached-instance/session-binding errors when attachment bytes are used in
        follow-up API calls (e.g., Linear uploads).
        """
        org_uuid: UUID = UUID(self.organization_id)
        conv_uuid: UUID = UUID(conversation_id)
        id_list: list[UUID] = []
        for aid in attachment_ids:
            try:
                id_list.append(UUID(aid))
            except ValueError:
                logger.warning("[linear] Skipping invalid attachment UUID: %s", aid)
        if not id_list:
            return []

        async with get_session(organization_id=self.organization_id) as session:
            stmt = (
                select(
                    ChatAttachment.id,
                    ChatAttachment.filename,
                    ChatAttachment.mime_type,
                    ChatAttachment.content,
                )
                .join(Conversation, ChatAttachment.conversation_id == Conversation.id)
                .where(Conversation.organization_id == org_uuid)
                .where(ChatAttachment.conversation_id == conv_uuid)
                .where(ChatAttachment.id.in_(id_list))
            )
            result = await session.execute(stmt)
            rows_db: list[tuple[UUID, str, str, bytes]] = list(result.all())

        by_id: dict[UUID, tuple[str, str, bytes]] = {
            row_id: (filename, mime_type, content)
            for row_id, filename, mime_type, content in rows_db
        }
        ordered: list[tuple[str, str, bytes]] = []
        for uid in id_list:
            row_data: tuple[str, str, bytes] | None = by_id.get(uid)
            if row_data is None:
                logger.warning(
                    "[linear] Attachment %s not found or not in conversation %s",
                    uid,
                    conversation_id,
                )
                continue
            ordered.append(row_data)

        logger.info(
            "[linear] Loaded %d/%d chat attachments for conversation=%s",
            len(ordered),
            len(id_list),
            conversation_id,
        )
        return ordered

    async def _markdown_block_from_chat_attachments(
        self,
        *,
        conversation_id: str | None,
        attachment_ids: Any | None,
    ) -> str | None:
        """Upload chat attachment bytes to Linear; return markdown (images/links) or None."""
        norm_attachment_ids: list[str] = _normalize_uuid_string_list(attachment_ids)
        if not norm_attachment_ids:
            return None
        if not conversation_id or not str(conversation_id).strip():
            logger.warning(
                "[linear] attachment_ids provided without conversation_id; skipping uploads",
            )
            return None
        rows: list[tuple[str, str, bytes]] = await self._load_chat_attachments_for_issue(
            conversation_id=str(conversation_id).strip(),
            attachment_ids=norm_attachment_ids,
        )
        if not rows:
            return None
        asset_lines: list[str] = []
        for fname, mime, content in rows:
            try:
                asset_url: str = await self._upload_bytes_to_linear(
                    data=content,
                    filename=fname,
                    content_type=mime or "application/octet-stream",
                )
            except Exception as exc:
                logger.error("[linear] Failed to upload attachment %s: %s", fname, exc)
                continue
            alt: str = _safe_markdown_alt_text(fname)
            if mime.lower().startswith("image/"):
                asset_lines.append(f"![{alt}]({asset_url})")
            else:
                asset_lines.append(f"[{alt}]({asset_url})")
        return "\n\n".join(asset_lines) if asset_lines else None

    # ── Write: Dispatch ─────────────────────────────────────────────────

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a record-level write operation."""
        if operation == "create_issue":
            filtered: dict[str, Any] = {
                k: v for k, v in data.items() if k in _CREATE_ISSUE_KWARGS
            }
            return await self.create_issue(**filtered)
        if operation == "update_issue":
            filtered_update: dict[str, Any] = {
                k: v for k, v in data.items() if k in _UPDATE_ISSUE_KWARGS
            }
            return await self.update_issue(**filtered_update)
        raise ValueError(f"Unknown write operation: {operation}")

    # ── Write: Create Issue ──────────────────────────────────────────────

    async def create_issue(
        self,
        *,
        team_key: str,
        title: str,
        description: str | None = None,
        priority: int | None = None,
        assignee_name: str | None = None,
        project_name: str | None = None,
        labels: list[str] | None = None,
        conversation_id: str | None = None,
        attachment_ids: Any | None = None,
    ) -> dict[str, Any]:
        """Create an issue in Linear via the issueCreate mutation.

        Accepts human-friendly parameters (team_key, assignee_name, etc.)
        and resolves them to Linear IDs internally.
        Optional ``attachment_ids`` are persisted chat attachment UUIDs; bytes are
        uploaded via Linear ``fileUpload`` and embedded in the description.
        """
        description_out: str | None = description
        attach_block: str | None = await self._markdown_block_from_chat_attachments(
            conversation_id=conversation_id,
            attachment_ids=attachment_ids,
        )
        if attach_block:
            description_out = (
                f"{description_out.rstrip()}\n\n{attach_block}"
                if description_out
                else attach_block
            )

        # Resolve team_key → team_id (required)
        team: dict[str, Any] | None = await self.resolve_team_by_key(team_key)
        if not team:
            raise ValueError(f"Team with key '{team_key}' not found")
        team_id: str = team["id"]

        # Resolve optional assignee_name → assignee_id
        assignee_id: str | None = None
        if assignee_name:
            assignee: dict[str, Any] | None = await self.resolve_assignee_by_name(assignee_name)
            if assignee:
                assignee_id = assignee["id"]
            else:
                logger.warning("Assignee '%s' not found, creating issue unassigned", assignee_name)

        # Resolve optional project_name → project_id
        project_id: str | None = None
        if project_name:
            project: dict[str, Any] | None = await self.resolve_project_by_name(project_name)
            if project:
                project_id = project["id"]
            else:
                logger.warning("Project '%s' not found, creating issue without project", project_name)

        # Resolve optional label names → label_ids
        label_ids: list[str] | None = None
        if labels:
            label_ids = await self.resolve_labels_by_names(labels)
            if not label_ids:
                logger.warning("No matching labels found for %s", labels)
                label_ids = None

        variables: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
        }
        if description_out:
            variables["description"] = description_out
        if priority is not None:
            variables["priority"] = priority
        if assignee_id:
            variables["assigneeId"] = assignee_id
        if project_id:
            variables["projectId"] = project_id
        if label_ids:
            variables["labelIds"] = label_ids

        mutation: str = """
        mutation CreateIssue($teamId: String!, $title: String!, $description: String,
                             $priority: Int, $assigneeId: String, $projectId: String,
                             $labelIds: [String!]) {
            issueCreate(input: {
                teamId: $teamId
                title: $title
                description: $description
                priority: $priority
                assigneeId: $assigneeId
                projectId: $projectId
                labelIds: $labelIds
            }) {
                success
                issue {
                    id
                    identifier
                    title
                    url
                    state {
                        name
                    }
                    priority
                    priorityLabel
                }
            }
        }
        """
        data: dict[str, Any] = await self._gql(mutation, variables)
        result: dict[str, Any] = data.get("issueCreate", {})
        if not result.get("success"):
            raise RuntimeError("Linear issueCreate mutation failed")

        issue: dict[str, Any] = result.get("issue", {})
        state: dict[str, Any] | None = issue.get("state")
        return {
            "linear_issue_id": issue["id"],
            "identifier": issue["identifier"],
            "title": issue["title"],
            "url": issue["url"],
            "state": state["name"] if state else None,
            "priority": issue.get("priority"),
            "priority_label": issue.get("priorityLabel"),
        }

    # ── Write: Update Issue ──────────────────────────────────────────────

    async def update_issue(
        self,
        *,
        issue_identifier: str,
        title: str | None = None,
        description: str | None = None,
        state_name: str | None = None,
        priority: int | None = None,
        assignee_name: str | None = None,
        project_name: str | None = None,
        conversation_id: str | None = None,
        attachment_ids: Any | None = None,
    ) -> dict[str, Any]:
        """Update an existing issue in Linear via the issueUpdate mutation.

        Accepts human-friendly parameters (issue_identifier, state_name, assignee_name,
        project_name) and resolves them to Linear IDs internally.
        Optional ``attachment_ids`` upload chat files and append markdown to the description
        (after any explicit ``description``, or after the current issue body if omitted).
        """
        # Resolve issue_identifier → issue_id
        issue_data: dict[str, Any] | None = await self.resolve_issue_by_identifier(issue_identifier)
        if not issue_data:
            raise ValueError(f"Issue '{issue_identifier}' not found")
        issue_id: str = issue_data["id"]
        team_id: str = issue_data["team"]["id"]

        attach_block: str | None = await self._markdown_block_from_chat_attachments(
            conversation_id=conversation_id,
            attachment_ids=attachment_ids,
        )
        effective_description: str | None = description
        if attach_block is not None:
            base_desc: str = (
                description
                if description is not None
                else (issue_data.get("description") or "")
            )
            if base_desc.strip():
                effective_description = f"{base_desc.rstrip()}\n\n{attach_block}"
            else:
                effective_description = attach_block

        # Resolve optional state_name → state_id
        state_id: str | None = None
        if state_name:
            state: dict[str, Any] | None = await self.resolve_state_by_name(team_id, state_name)
            if state:
                state_id = state["id"]
            else:
                logger.warning("State '%s' not found for team, skipping state update", state_name)

        # Resolve optional assignee_name → assignee_id
        assignee_id: str | None = None
        if assignee_name:
            assignee: dict[str, Any] | None = await self.resolve_assignee_by_name(assignee_name)
            if assignee:
                assignee_id = assignee["id"]
            else:
                logger.warning("Assignee '%s' not found, skipping assignee update", assignee_name)

        # Resolve optional project_name → project_id
        project_id: str | None = None
        if project_name:
            project: dict[str, Any] | None = await self.resolve_project_by_name(project_name)
            if project:
                project_id = project["id"]
            else:
                logger.warning("Project '%s' not found, skipping project update", project_name)

        input_fields: dict[str, Any] = {}
        if title is not None:
            input_fields["title"] = title
        if effective_description is not None:
            input_fields["description"] = effective_description
        if state_id is not None:
            input_fields["stateId"] = state_id
        if priority is not None:
            input_fields["priority"] = priority
        if assignee_id is not None:
            input_fields["assigneeId"] = assignee_id
        if project_id is not None:
            input_fields["projectId"] = project_id

        if not input_fields:
            raise ValueError("At least one field to update must be provided")

        # Build dynamic input fields string
        input_parts: list[str] = []
        variables: dict[str, Any] = {"issueId": issue_id}
        for key, value in input_fields.items():
            var_name: str = f"input_{key}"
            variables[var_name] = value
            gql_type: str = "String"
            if key == "priority":
                gql_type = "Int"
            input_parts.append(f"{key}: ${var_name}")

        # Build variable declarations
        var_decls: list[str] = ["$issueId: String!"]
        for key, value in input_fields.items():
            var_name = f"input_{key}"
            gql_type = "String"
            if key == "priority":
                gql_type = "Int"
            var_decls.append(f"${var_name}: {gql_type}")

        mutation: str = f"""
        mutation UpdateIssue({", ".join(var_decls)}) {{
            issueUpdate(id: $issueId, input: {{ {", ".join(input_parts)} }}) {{
                success
                issue {{
                    id
                    identifier
                    title
                    url
                    state {{
                        name
                    }}
                    priority
                    priorityLabel
                }}
            }}
        }}
        """
        data: dict[str, Any] = await self._gql(mutation, variables)
        result: dict[str, Any] = data.get("issueUpdate", {})
        if not result.get("success"):
            raise RuntimeError("Linear issueUpdate mutation failed")

        issue: dict[str, Any] = result.get("issue", {})
        state: dict[str, Any] | None = issue.get("state")
        return {
            "linear_issue_id": issue["id"],
            "identifier": issue["identifier"],
            "title": issue["title"],
            "url": issue["url"],
            "state": state["name"] if state else None,
            "priority": issue.get("priority"),
            "priority_label": issue.get("priorityLabel"),
        }

    # ── Read: Search Issues ──────────────────────────────────────────────

    async def search_issues(
        self,
        *,
        query_text: str,
        team_key: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search issues in Linear using the searchIssues query."""
        # Resolve team_key to team_id if provided
        team_id: str | None = None
        if team_key:
            team_id = await self.resolve_team_by_key(team_key)

        gql_query: str = """
        query SearchIssues($term: String!, $first: Int, $teamId: String, $includeComments: Boolean) {
            searchIssues(term: $term, first: $first, teamId: $teamId, includeComments: $includeComments) {
                nodes {
                    id
                    identifier
                    title
                    description
                    url
                    state {
                        name
                        type
                    }
                    priority
                    priorityLabel
                    assignee {
                        name
                    }
                    team {
                        key
                        name
                    }
                    project {
                        name
                    }
                    labels {
                        nodes {
                            name
                        }
                    }
                    createdAt
                    updatedAt
                }
            }
        }
        """
        variables: dict[str, Any] = {
            "term": query_text,
            "first": min(limit, 50),
            "includeComments": True,
        }
        if team_id:
            variables["teamId"] = team_id

        data: dict[str, Any] = await self._gql(gql_query, variables)
        nodes: list[dict[str, Any]] = (
            data.get("searchIssues", {}).get("nodes", [])
        )

        results: list[dict[str, Any]] = []
        for issue in nodes:
            state: dict[str, Any] | None = issue.get("state")
            assignee: dict[str, Any] | None = issue.get("assignee")
            team: dict[str, Any] | None = issue.get("team")
            project: dict[str, Any] | None = issue.get("project")
            label_nodes: list[dict[str, Any]] = (
                issue.get("labels", {}).get("nodes", [])
            )
            results.append({
                "identifier": issue["identifier"],
                "title": issue["title"],
                "description": (issue.get("description") or "")[:500],
                "url": issue["url"],
                "state": state["name"] if state else None,
                "state_type": state["type"] if state else None,
                "priority_label": issue.get("priorityLabel"),
                "assignee": assignee["name"] if assignee else None,
                "team": f"{team['key']} ({team['name']})" if team else None,
                "project": project["name"] if project else None,
                "labels": [ln["name"] for ln in label_nodes],
                "created_at": issue.get("createdAt"),
                "updated_at": issue.get("updatedAt"),
            })

        return results

    # ── Read: List Teams ─────────────────────────────────────────────────

    async def list_teams(self) -> list[dict[str, Any]]:
        """Quick query to list all teams (for tool resolution)."""
        data: dict[str, Any] = await self._gql("""
        query {
            teams {
                nodes {
                    id
                    name
                    key
                }
            }
        }
        """)
        return data.get("teams", {}).get("nodes", [])

    # ── Read: List Workflow States ────────────────────────────────────────

    async def list_workflow_states(self, team_id: str) -> list[dict[str, Any]]:
        """Fetch all workflow states for a team (for state resolution)."""
        data: dict[str, Any] = await self._gql(
            """
            query WorkflowStates($teamId: String!) {
                team(id: $teamId) {
                    states {
                        nodes {
                            id
                            name
                            type
                            position
                        }
                    }
                }
            }
            """,
            {"teamId": team_id},
        )
        states: list[dict[str, Any]] = (
            data.get("team", {}).get("states", {}).get("nodes", [])
        )
        return sorted(states, key=lambda s: s.get("position", 0))

    # ── Read: List Projects ──────────────────────────────────────────────

    async def list_projects(self) -> list[dict[str, Any]]:
        """Quick query to list all active projects."""
        data: dict[str, Any] = await self._gql("""
        query {
            projects(first: 100, filter: { state: { nin: ["canceled"] } }) {
                nodes {
                    id
                    name
                    state
                }
            }
        }
        """)
        return data.get("projects", {}).get("nodes", [])

    # ── Resolve helpers (human-friendly names → Linear IDs) ──────────────

    async def resolve_team_by_key(self, team_key: str) -> dict[str, Any] | None:
        """Find a team by its key (e.g. 'ENG')."""
        teams: list[dict[str, Any]] = await self.list_teams()
        key_upper: str = team_key.upper().strip()
        for team in teams:
            if team["key"].upper() == key_upper:
                return team
        return None

    async def resolve_project_by_name(self, project_name: str) -> dict[str, Any] | None:
        """Find a project by name (case-insensitive)."""
        projects: list[dict[str, Any]] = await self.list_projects()
        name_lower: str = project_name.lower().strip()
        for project in projects:
            if project["name"].lower() == name_lower:
                return project
        return None

    async def resolve_state_by_name(
        self, team_id: str, state_name: str
    ) -> dict[str, Any] | None:
        """Find a workflow state by name/type within a team."""
        states: list[dict[str, Any]] = await self.list_workflow_states(team_id)
        name_lower: str = state_name.lower().strip()

        # 1) Exact state name match
        for state in states:
            if (state.get("name") or "").lower() == name_lower:
                return state

        # 2) Alias match on state type to support common task language like "todo"
        type_aliases: dict[str, set[str]] = {
            "backlog": {"backlog", "triage"},
            "unstarted": {"todo", "to do", "open", "new", "unstarted", "not started"},
            "started": {"in progress", "doing", "active", "started", "wip"},
            "completed": {"done", "complete", "completed", "closed", "resolved"},
            "canceled": {"canceled", "cancelled", "wontfix", "won't fix"},
        }
        target_type: str | None = None
        for state_type, aliases in type_aliases.items():
            if name_lower in aliases:
                target_type = state_type
                break

        if target_type:
            for state in states:
                if (state.get("type") or "").lower() == target_type:
                    logger.info(
                        "Resolved Linear state '%s' to workflow type '%s' (%s)",
                        state_name,
                        target_type,
                        state.get("name"),
                    )
                    return state
        return None

    async def _resolve_current_user_assignee_candidates(self) -> list[str]:
        """Return identity tokens for the current user that may match a Linear assignee."""
        if not self.user_id:
            return []

        user_uuid: UUID = UUID(self.user_id)
        org_uuid: UUID = UUID(self.organization_id)
        candidates: list[str] = []

        async with get_session(organization_id=self.organization_id) as session:
            user_result = await session.execute(select(User).where(User.id == user_uuid))
            user: User | None = user_result.scalar_one_or_none()
            if user:
                if user.name:
                    candidates.append(user.name)
                if user.email:
                    candidates.append(user.email)

            mapping_rows = await session.execute(
                select(ExternalIdentityMapping).where(
                    ExternalIdentityMapping.organization_id == org_uuid,
                    ExternalIdentityMapping.user_id == user_uuid,
                    ExternalIdentityMapping.source == "linear",
                )
            )
            mappings: list[ExternalIdentityMapping] = list(mapping_rows.scalars().all())
            for mapping in mappings:
                if mapping.external_userid:
                    candidates.append(mapping.external_userid)
                if mapping.external_email:
                    candidates.append(mapping.external_email)

        deduped: list[str] = []
        seen: set[str] = set()
        for raw in candidates:
            token: str = raw.strip()
            if not token:
                continue
            key: str = token.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(token)

        logger.info("Linear 'me' assignee resolution candidates=%s", deduped)
        return deduped

    async def list_users(self) -> list[dict[str, Any]]:
        """List users in the Linear workspace (paginated)."""
        query: str = """
        query Users($after: String) {
            users(first: 100, after: $after) {
                nodes {
                    id
                    name
                    email
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        users: list[dict[str, Any]] = await self._gql_paginated(query, ["users"])
        logger.debug("Loaded %d Linear users for assignee resolution", len(users))
        return users

    async def resolve_assignee_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a Linear user by email or display name.

        Historically this accepted only exact display-name matches, which was brittle
        and caused assignment failures for common inputs (email, partial name).
        """
        needle: str = name.strip()
        if not needle:
            return None

        users: list[dict[str, Any]] = await self.list_users()

        needles: list[str] = [needle]
        if needle.lower() in {"me", "myself", "self", "@me"}:
            me_tokens: list[str] = await self._resolve_current_user_assignee_candidates()
            needles = [*me_tokens, needle]

        for candidate in needles:
            matched = self._resolve_assignee_from_users(users, candidate)
            if matched:
                if candidate != needle:
                    logger.info(
                        "Resolved Linear assignee '%s' using candidate '%s'",
                        name,
                        candidate,
                    )
                return matched

        logger.warning("Linear assignee '%s' not found", name)
        return None

    def _resolve_assignee_from_users(
        self,
        users: list[dict[str, Any]],
        needle: str,
    ) -> dict[str, Any] | None:
        """Resolve a single assignee token against a user list."""
        needle_lower: str = needle.strip().lower()
        if not needle_lower:
            return None

        # 0) Exact Linear user ID match
        for user in users:
            linear_user_id: str = (user.get("id") or "").strip().lower()
            if linear_user_id and linear_user_id == needle_lower:
                logger.info("Resolved Linear assignee '%s' by exact user id", needle)
                return user

        # 1) Exact email match (most stable identifier)
        for user in users:
            email: str = (user.get("email") or "").strip().lower()
            if email and email == needle_lower:
                logger.info("Resolved Linear assignee '%s' by exact email", needle)
                return user

        # 2) Exact display-name match
        for user in users:
            user_name: str = (user.get("name") or "").strip().lower()
            if user_name and user_name == needle_lower:
                logger.info("Resolved Linear assignee '%s' by exact name", needle)
                return user

        # 3) Unique prefix match (e.g. "alex" -> "Alex Kim")
        prefix_matches: list[dict[str, Any]] = [
            user
            for user in users
            if (user.get("name") or "").strip().lower().startswith(needle_lower)
        ]
        if len(prefix_matches) == 1:
            logger.info("Resolved Linear assignee '%s' by unique name prefix", needle)
            return prefix_matches[0]

        # 4) Unique contains match as a final fallback
        contains_matches: list[dict[str, Any]] = [
            user
            for user in users
            if needle_lower in (user.get("name") or "").strip().lower()
        ]
        if len(contains_matches) == 1:
            logger.info("Resolved Linear assignee '%s' by unique contains match", needle)
            return contains_matches[0]

        if len(prefix_matches) > 1 or len(contains_matches) > 1:
            logger.warning(
                "Linear assignee lookup for '%s' is ambiguous (prefix=%d contains=%d)",
                needle,
                len(prefix_matches),
                len(contains_matches),
            )
        return None

    async def resolve_labels_by_names(self, label_names: list[str]) -> list[str]:
        """Resolve label names to Linear label IDs."""
        if not label_names:
            return []
        data: dict[str, Any] = await self._gql("""
        query {
            issueLabels {
                nodes {
                    id
                    name
                }
            }
        }
        """)
        all_labels: list[dict[str, Any]] = data.get("issueLabels", {}).get("nodes", [])
        label_map: dict[str, str] = {lbl["name"].lower(): lbl["id"] for lbl in all_labels}
        return [label_map[name.lower()] for name in label_names if name.lower() in label_map]

    async def resolve_issue_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        """Find an issue by its identifier (e.g. 'ENG-123')."""
        data: dict[str, Any] = await self._gql(
            """
            query IssueByIdentifier($identifier: String!) {
                issue(id: $identifier) {
                    id
                    identifier
                    description
                    team { id key }
                }
            }
            """,
            {"identifier": identifier},
        )
        return data.get("issue")

    # ── LISTEN: Inbound webhooks (issue done → workflow triggers) ───────

    @staticmethod
    def verify_webhook(raw_body: bytes, headers: dict[str, str], secret: str) -> bool:
        """Verify Linear webhook HMAC-SHA256 signature (Linear-Signature header)."""
        signature_header: str | None = headers.get("linear-signature") or headers.get("Linear-Signature")
        if not signature_header or not secret:
            return False
        try:
            expected: bytes = hmac.new(
                secret.encode("utf-8"), raw_body, hashlib.sha256
            ).digest()
            received: bytes = bytes.fromhex(signature_header)
            return hmac.compare_digest(expected, received)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def process_webhook_payload(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """
        Parse Linear webhook JSON; return [(event_type, data), ...] for workflow events.
        Rejects payloads with webhookTimestamp outside 60s window. Emits linear.issue.done
        when an issue is in a completed (Done) state.
        """
        events: list[tuple[str, dict[str, Any]]] = []
        ts_ms: Any = payload.get("webhookTimestamp")
        if ts_ms is not None:
            try:
                now_ms: int = int(time.time() * 1000)
                if abs(now_ms - int(ts_ms)) > 60 * 1000:
                    return events
            except (TypeError, ValueError):
                return events
        if payload.get("type") != "Issue":
            return events
        data: dict[str, Any] | None = payload.get("data")
        state: dict[str, Any] | None = (data or {}).get("state")
        if not state or state.get("type") != "completed":
            return events
        events.append(
            (
                LINEAR_ISSUE_DONE_EVENT,
                {
                    "action": payload.get("action"),
                    "type": payload.get("type"),
                    "createdAt": payload.get("createdAt"),
                    "url": payload.get("url"),
                    "data": data,
                    "updatedFrom": payload.get("updatedFrom"),
                },
            )
        )
        return events

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """
        Handle an inbound Linear webhook event (LISTEN capability).
        Workflow triggers are driven by the generic connector webhook route.
        """
        if event_type == LINEAR_ISSUE_DONE_EVENT:
            logger.info(
                "[linear] Received issue.done for org %s: %s",
                self.organization_id,
                (payload.get("data") or {}).get("identifier"),
            )

    # ── CRM no-ops (BaseConnector requires these) ────────────────────────

    async def sync_deals(self) -> int:
        """Not applicable for Linear."""
        return 0

    async def sync_accounts(self) -> int:
        """Not applicable for Linear."""
        return 0

    async def sync_contacts(self) -> int:
        """Not applicable for Linear."""
        return 0

    async def sync_activities(self) -> int:
        """Not applicable for Linear."""
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Not applicable for Linear."""
        raise NotImplementedError("Linear connector does not support deals")

    # ── Override sync_all with Linear-specific flow ──────────────────────

    async def sync_all(self) -> dict[str, int]:
        """
        Run all Linear sync operations.

        Order: teams → projects → issues.
        """
        await self.ensure_sync_active("sync_all:start")

        teams_count: int = await self.sync_teams()
        await self.ensure_sync_active("sync_all:after_teams")

        projects_count: int = await self.sync_projects()
        await self.ensure_sync_active("sync_all:after_projects")

        issues_count: int = await self.sync_issues()

        result: dict[str, int] = {
            "teams": teams_count,
            "projects": projects_count,
            "issues": issues_count,
        }
        return result


# ── Date parsing helpers ─────────────────────────────────────────────────

def _parse_date(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD date string from Linear."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def _parse_datetime(dt_str: str) -> datetime:
    """Parse an ISO-8601 datetime string from Linear."""
    if not dt_str:
        return datetime.utcnow()
    cleaned: str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned).replace(tzinfo=None)


def _parse_datetime_optional(dt_str: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime that may be None."""
    if not dt_str:
        return None
    cleaned: str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned).replace(tzinfo=None)
