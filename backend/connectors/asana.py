"""
Asana connector – syncs teams, projects, and tasks via the REST API.

Like the Linear connector, Asana data maps to tracker_teams / tracker_projects /
tracker_issues rather than CRM objects. The CRM abstract methods are implemented
as no-ops; sync_all() is overridden to run Asana-specific sync operations.

OAuth is handled through Nango (Asana OAuth App).
Asana API docs: https://developers.asana.com/reference/rest-api-reference
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from connectors.base import BaseConnector
from connectors.registry import (
    AuthType, Capability, ConnectorMeta, ConnectorScope, WriteOperation,
)
from models.database import get_session
from models.tracker_issue import TrackerIssue
from models.tracker_project import TrackerProject
from models.tracker_team import TrackerTeam

logger = logging.getLogger(__name__)

ASANA_API_BASE: str = "https://app.asana.com/api/1.0"

# ── Priority mapping ────────────────────────────────────────────────────
# Asana custom-field enum values don't have numeric priorities built-in;
# we normalise the text labels that appear in the default "Priority" field.
PRIORITY_MAP: dict[str, tuple[int, str]] = {
    "high": (2, "High"),
    "medium": (3, "Medium"),
    "low": (4, "Low"),
}


class AsanaConnector(BaseConnector):
    """Connector for Asana – teams, projects, and tasks."""

    source_system: str = "asana"
    meta = ConnectorMeta(
        name="Asana",
        slug="asana",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["teams", "projects", "issues"],
        capabilities=[Capability.SYNC, Capability.WRITE],
        write_operations=[
            WriteOperation(
                name="create_issue", entity_type="issue",
                description="Create an Asana task",
                parameters=[
                    {"name": "team_key", "type": "string", "required": True, "description": "Workspace/team GID"},
                    {"name": "title", "type": "string", "required": True, "description": "Task name"},
                    {"name": "description", "type": "string", "required": False, "description": "Task description"},
                    {"name": "project_name", "type": "string", "required": False, "description": "Project name to add task to"},
                    {"name": "assignee_name", "type": "string", "required": False, "description": "Assignee display name"},
                    {"name": "due_date", "type": "string", "required": False, "description": "Due date (YYYY-MM-DD)"},
                ],
            ),
            WriteOperation(
                name="update_issue", entity_type="issue",
                description="Update an existing Asana task",
                parameters=[
                    {"name": "issue_identifier", "type": "string", "required": True, "description": "Asana task GID"},
                    {"name": "title", "type": "string", "required": False, "description": "New name"},
                    {"name": "description", "type": "string", "required": False, "description": "New description"},
                    {"name": "state_name", "type": "string", "required": False, "description": "New state (completed / not completed)"},
                    {"name": "assignee_name", "type": "string", "required": False, "description": "New assignee"},
                    {"name": "due_date", "type": "string", "required": False, "description": "New due date (YYYY-MM-DD)"},
                ],
            ),
        ],
        nango_integration_id="asana",
        description="Asana – teams, projects, and task management",
    )

    def __init__(
        self, organization_id: str, user_id: Optional[str] = None
    ) -> None:
        super().__init__(organization_id, user_id)
        self._workspace_gid: Optional[str] = None

    # ── REST helpers ─────────────────────────────────────────────────────

    async def _get_headers(self) -> dict[str, str]:
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GET request against the Asana REST API."""
        headers: dict[str, str] = await self._get_headers()
        url: str = f"{ASANA_API_BASE}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.get(
                url,
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()

        if "errors" in result:
            error_msg: str = result["errors"][0].get("message", "Unknown API error")
            logger.error("Asana API error: %s", error_msg)
            raise RuntimeError(f"Asana API error: {error_msg}")

        return result

    async def _post(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a POST request against the Asana REST API."""
        headers: dict[str, str] = await self._get_headers()
        url: str = f"{ASANA_API_BASE}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.post(
                url,
                headers=headers,
                json=json_body,
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()

        if "errors" in result:
            error_msg = result["errors"][0].get("message", "Unknown API error")
            logger.error("Asana API error: %s", error_msg)
            raise RuntimeError(f"Asana API error: {error_msg}")

        return result

    async def _put(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a PUT request against the Asana REST API."""
        headers: dict[str, str] = await self._get_headers()
        url: str = f"{ASANA_API_BASE}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.put(
                url,
                headers=headers,
                json=json_body,
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()

        if "errors" in result:
            error_msg = result["errors"][0].get("message", "Unknown API error")
            logger.error("Asana API error: %s", error_msg)
            raise RuntimeError(f"Asana API error: {error_msg}")

        return result

    async def _get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Paginate through an Asana collection (offset-based).

        Asana returns:
          { data: [...], next_page: { offset: "...", uri: "..." } | null }
        """
        all_items: list[dict[str, Any]] = []
        current_params: dict[str, Any] = dict(params or {})

        for _ in range(max_pages):
            result: dict[str, Any] = await self._get(path, current_params)
            data: list[dict[str, Any]] = result.get("data", [])
            if not data:
                break
            all_items.extend(data)

            next_page: dict[str, Any] | None = result.get("next_page")
            if not next_page or not next_page.get("offset"):
                break
            current_params["offset"] = next_page["offset"]

        return all_items

    # ── Workspace resolution ─────────────────────────────────────────────

    async def _get_workspace_gid(self) -> str:
        """Resolve and cache the workspace GID (uses the first workspace)."""
        if self._workspace_gid:
            return self._workspace_gid

        result: dict[str, Any] = await self._get("/workspaces", {"limit": 10})
        workspaces: list[dict[str, Any]] = result.get("data", [])
        if not workspaces:
            raise RuntimeError("No Asana workspaces found for this account")

        # Prefer the first non-personal workspace (organizations),
        # but fall back to the first workspace available.
        for ws in workspaces:
            if ws.get("is_organization", False):
                self._workspace_gid = ws["gid"]
                return self._workspace_gid

        self._workspace_gid = workspaces[0]["gid"]
        return self._workspace_gid

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
        """Fetch all teams from Asana and upsert into tracker_teams."""
        org_uuid: UUID = UUID(self.organization_id)
        integration = await self._get_integration()
        integration_id: UUID = integration.id
        workspace_gid: str = await self._get_workspace_gid()

        teams: list[dict[str, Any]] = await self._get_paginated(
            f"/organizations/{workspace_gid}/teams",
            {"limit": 100},
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for t in teams:
                # Fetch full team detail for description
                detail: dict[str, Any] = (
                    await self._get(f"/teams/{t['gid']}")
                ).get("data", {})

                stmt = pg_insert(TrackerTeam).values(
                    organization_id=org_uuid,
                    integration_id=integration_id,
                    source_system="asana",
                    source_id=t["gid"],
                    name=t.get("name", ""),
                    key=None,  # Asana teams have no short key
                    description=detail.get("description"),
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "name": t.get("name", ""),
                        "description": detail.get("description"),
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)
                count += 1
            await session.commit()

        logger.info("Synced %d Asana teams for org %s", count, self.organization_id)
        return count

    # ── Sync: Projects ───────────────────────────────────────────────────

    async def sync_projects(self) -> int:
        """Fetch all projects from Asana and upsert into tracker_projects."""
        org_uuid: UUID = UUID(self.organization_id)
        workspace_gid: str = await self._get_workspace_gid()

        projects: list[dict[str, Any]] = await self._get_paginated(
            "/projects",
            {
                "workspace": workspace_gid,
                "limit": 100,
                "opt_fields": (
                    "gid,name,notes,current_status_update,current_status_update.text,"
                    "due_on,start_on,permalink_url,owner.name,"
                    "team.gid,archived,completed"
                ),
            },
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for p in projects:
                # Skip archived projects
                if p.get("archived", False):
                    continue

                owner: dict[str, Any] | None = p.get("owner")
                team_data: dict[str, Any] | None = p.get("team")
                team_ids: list[str] = [team_data["gid"]] if team_data else []

                # Map Asana project status
                state: str = "active"
                if p.get("completed", False):
                    state = "completed"
                status_update: dict[str, Any] | None = p.get("current_status_update")
                if status_update:
                    status_text: str | None = status_update.get("text")
                    if status_text:
                        state = status_text[:30]

                stmt = pg_insert(TrackerProject).values(
                    organization_id=org_uuid,
                    source_system="asana",
                    source_id=p["gid"],
                    name=p.get("name", ""),
                    description=(p.get("notes") or "")[:5000] or None,
                    state=state,
                    progress=None,
                    target_date=_parse_date(p.get("due_on")),
                    start_date=_parse_date(p.get("start_on")),
                    url=p.get("permalink_url", ""),
                    lead_name=owner["name"] if owner else None,
                    team_ids=team_ids or None,
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "name": p.get("name", ""),
                        "description": (p.get("notes") or "")[:5000] or None,
                        "state": state,
                        "target_date": _parse_date(p.get("due_on")),
                        "start_date": _parse_date(p.get("start_on")),
                        "url": p.get("permalink_url", ""),
                        "lead_name": owner["name"] if owner else None,
                        "team_ids": team_ids or None,
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)
                count += 1
            await session.commit()

        logger.info("Synced %d Asana projects for org %s", count, self.organization_id)
        return count

    # ── Sync: Tasks (Issues) ─────────────────────────────────────────────

    async def sync_issues(self) -> int:
        """Fetch tasks from Asana projects and upsert into tracker_issues."""
        org_uuid: UUID = UUID(self.organization_id)

        # Build lookup of source_id → internal UUID for teams
        team_map: dict[str, UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(TrackerTeam.source_id, TrackerTeam.id).where(
                    TrackerTeam.organization_id == org_uuid,
                    TrackerTeam.source_system == "asana",
                )
            )
            for row in result.all():
                team_map[row[0]] = row[1]

        # Build lookup of source_id → (internal UUID, team_ids) for projects
        project_map: dict[str, UUID] = {}
        project_team_map: dict[str, list[str]] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(
                    TrackerProject.source_id,
                    TrackerProject.id,
                    TrackerProject.team_ids,
                ).where(
                    TrackerProject.organization_id == org_uuid,
                    TrackerProject.source_system == "asana",
                )
            )
            for row in result.all():
                project_map[row[0]] = row[1]
                project_team_map[row[0]] = row[2] or []

        # Iterate over each synced project and fetch its tasks
        task_fields: str = (
            "gid,name,notes,completed,completed_at,created_at,modified_at,"
            "due_on,assignee.name,assignee.email,created_by.name,"
            "memberships.project.gid,memberships.section.name,"
            "tags.name,permalink_url,resource_subtype,"
            "custom_fields.name,custom_fields.display_value"
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for project_source_id, internal_project_id in project_map.items():
                # Determine team for this project
                project_team_ids: list[str] = project_team_map.get(
                    project_source_id, []
                )
                internal_team_id: UUID | None = None
                for tid in project_team_ids:
                    if tid in team_map:
                        internal_team_id = team_map[tid]
                        break

                if not internal_team_id:
                    # Fall back to first available team
                    if team_map:
                        internal_team_id = next(iter(team_map.values()))
                    else:
                        continue  # No teams synced yet

                tasks: list[dict[str, Any]] = await self._get_paginated(
                    f"/projects/{project_source_id}/tasks",
                    {"limit": 100, "opt_fields": task_fields},
                )

                for task in tasks:
                    assignee: dict[str, Any] | None = task.get("assignee")
                    creator: dict[str, Any] | None = task.get("created_by")
                    tag_nodes: list[dict[str, Any]] = task.get("tags") or []
                    labels: list[str] = [
                        t["name"] for t in tag_nodes if t.get("name")
                    ]

                    # Determine section name (used as state)
                    state_name: str | None = None
                    memberships: list[dict[str, Any]] = (
                        task.get("memberships") or []
                    )
                    for m in memberships:
                        section: dict[str, Any] | None = m.get("section")
                        if section and section.get("name"):
                            state_name = section["name"]
                            break

                    # Map completion status to state_type
                    is_completed: bool = task.get("completed", False)
                    state_type: str = _resolve_state_type(
                        is_completed, state_name
                    )

                    # Extract priority from custom fields
                    priority: int | None = None
                    priority_label: str | None = None
                    custom_fields: list[dict[str, Any]] = (
                        task.get("custom_fields") or []
                    )
                    for cf in custom_fields:
                        cf_name: str = cf.get("name", "")
                        if cf_name.lower() == "priority" and cf.get(
                            "display_value"
                        ):
                            display_val: str = cf["display_value"].lower()
                            mapped: tuple[int, str] | None = PRIORITY_MAP.get(
                                display_val
                            )
                            if mapped:
                                priority, priority_label = mapped
                            break

                    # Asana doesn't have a short identifier like Linear's ENG-123
                    identifier: str = f"ASANA-{task['gid'][-8:]}"

                    created_at_str: str | None = task.get("created_at")
                    created_date: datetime = (
                        _parse_datetime(created_at_str)
                        if created_at_str
                        else datetime.utcnow()
                    )

                    stmt = pg_insert(TrackerIssue).values(
                        organization_id=org_uuid,
                        team_id=internal_team_id,
                        source_system="asana",
                        source_id=task["gid"],
                        identifier=identifier,
                        title=task.get("name", ""),
                        description=(task.get("notes") or "")[:5000] or None,
                        state_name=state_name,
                        state_type=state_type,
                        priority=priority,
                        priority_label=priority_label,
                        assignee_name=(
                            assignee["name"] if assignee else None
                        ),
                        assignee_email=(
                            assignee.get("email") if assignee else None
                        ),
                        creator_name=(
                            creator["name"] if creator else None
                        ),
                        project_id=internal_project_id,
                        labels=labels or None,
                        estimate=None,
                        url=task.get("permalink_url", ""),
                        due_date=_parse_date(task.get("due_on")),
                        created_date=created_date,
                        updated_date=_parse_datetime_optional(
                            task.get("modified_at")
                        ),
                        completed_date=_parse_datetime_optional(
                            task.get("completed_at")
                        ),
                        cancelled_date=None,
                    ).on_conflict_do_update(
                        index_elements=[
                            "organization_id",
                            "source_system",
                            "source_id",
                        ],
                        set_={
                            "team_id": internal_team_id,
                            "identifier": identifier,
                            "title": task.get("name", ""),
                            "description": (
                                (task.get("notes") or "")[:5000] or None
                            ),
                            "state_name": state_name,
                            "state_type": state_type,
                            "priority": priority,
                            "priority_label": priority_label,
                            "assignee_name": (
                                assignee["name"] if assignee else None
                            ),
                            "assignee_email": (
                                assignee.get("email") if assignee else None
                            ),
                            "creator_name": (
                                creator["name"] if creator else None
                            ),
                            "project_id": internal_project_id,
                            "labels": labels or None,
                            "url": task.get("permalink_url", ""),
                            "due_date": _parse_date(task.get("due_on")),
                            "updated_date": _parse_datetime_optional(
                                task.get("modified_at")
                            ),
                            "completed_date": _parse_datetime_optional(
                                task.get("completed_at")
                            ),
                            "updated_at": datetime.utcnow(),
                        },
                    )
                    await session.execute(stmt)
                    count += 1

            await session.commit()

        logger.info(
            "Synced %d Asana tasks for org %s", count, self.organization_id
        )
        return count

    # ── Write: Dispatch ─────────────────────────────────────────────────

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a record-level write operation."""
        if operation == "create_issue":
            return await self.create_issue(**data)
        if operation == "update_issue":
            return await self.update_issue(**data)
        raise ValueError(f"Unknown write operation: {operation}")

    # ── Write: Create Task ───────────────────────────────────────────────

    async def create_issue(
        self,
        *,
        project_gid: str,
        name: str,
        notes: str | None = None,
        assignee: str | None = None,
        due_on: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in Asana."""
        workspace_gid: str = await self._get_workspace_gid()
        body: dict[str, Any] = {
            "data": {
                "workspace": workspace_gid,
                "projects": [project_gid],
                "name": name,
            }
        }
        if notes:
            body["data"]["notes"] = notes
        if assignee:
            body["data"]["assignee"] = assignee
        if due_on:
            body["data"]["due_on"] = due_on

        result: dict[str, Any] = await self._post("/tasks", body)
        task: dict[str, Any] = result.get("data", {})

        return {
            "asana_task_gid": task.get("gid"),
            "name": task.get("name"),
            "url": task.get("permalink_url", ""),
            "completed": task.get("completed", False),
        }

    # ── Write: Update Task ───────────────────────────────────────────────

    async def update_issue(
        self,
        *,
        task_gid: str,
        name: str | None = None,
        notes: str | None = None,
        completed: bool | None = None,
        assignee: str | None = None,
        due_on: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing task in Asana."""
        data: dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if notes is not None:
            data["notes"] = notes
        if completed is not None:
            data["completed"] = completed
        if assignee is not None:
            data["assignee"] = assignee
        if due_on is not None:
            data["due_on"] = due_on

        if not data:
            raise ValueError("At least one field to update must be provided")

        result: dict[str, Any] = await self._put(
            f"/tasks/{task_gid}", {"data": data}
        )
        task: dict[str, Any] = result.get("data", {})

        return {
            "asana_task_gid": task.get("gid"),
            "name": task.get("name"),
            "url": task.get("permalink_url", ""),
            "completed": task.get("completed", False),
        }

    # ── Read: Search Tasks ───────────────────────────────────────────────

    async def search_issues(
        self,
        *,
        query_text: str,
        project_gid: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search tasks in Asana using the workspace search endpoint."""
        workspace_gid: str = await self._get_workspace_gid()

        params: dict[str, Any] = {
            "text": query_text,
            "limit": min(limit, 100),
            "opt_fields": (
                "gid,name,notes,completed,completed_at,permalink_url,"
                "assignee.name,projects.name,tags.name,created_at,modified_at"
            ),
        }
        if project_gid:
            params["projects.any"] = project_gid

        result: dict[str, Any] = await self._get(
            f"/workspaces/{workspace_gid}/tasks/search", params
        )
        tasks: list[dict[str, Any]] = result.get("data", [])

        results: list[dict[str, Any]] = []
        for task in tasks:
            assignee_data: dict[str, Any] | None = task.get("assignee")
            project_nodes: list[dict[str, Any]] = task.get("projects") or []
            tag_nodes: list[dict[str, Any]] = task.get("tags") or []

            results.append(
                {
                    "gid": task["gid"],
                    "name": task.get("name"),
                    "notes": (task.get("notes") or "")[:500],
                    "url": task.get("permalink_url", ""),
                    "completed": task.get("completed", False),
                    "assignee": (
                        assignee_data["name"] if assignee_data else None
                    ),
                    "projects": [p["name"] for p in project_nodes if p.get("name")],
                    "tags": [t["name"] for t in tag_nodes if t.get("name")],
                    "created_at": task.get("created_at"),
                    "modified_at": task.get("modified_at"),
                }
            )

        return results

    # ── Read: List Teams ─────────────────────────────────────────────────

    async def list_teams(self) -> list[dict[str, Any]]:
        """Quick query to list all teams."""
        workspace_gid: str = await self._get_workspace_gid()
        result: dict[str, Any] = await self._get(
            f"/organizations/{workspace_gid}/teams",
            {"limit": 100},
        )
        return result.get("data", [])

    # ── Read: List Projects ──────────────────────────────────────────────

    async def list_projects(self) -> list[dict[str, Any]]:
        """Quick query to list all active projects."""
        workspace_gid: str = await self._get_workspace_gid()
        result: dict[str, Any] = await self._get(
            "/projects",
            {
                "workspace": workspace_gid,
                "archived": "false",
                "limit": 100,
                "opt_fields": "gid,name",
            },
        )
        return result.get("data", [])

    # ── Read: List Sections (workflow states for a project) ──────────────

    async def list_sections(self, project_gid: str) -> list[dict[str, Any]]:
        """Fetch all sections (columns) for a project."""
        result: dict[str, Any] = await self._get(
            f"/projects/{project_gid}/sections",
            {"opt_fields": "gid,name"},
        )
        return result.get("data", [])

    # ── Read: List Users ─────────────────────────────────────────────────

    async def list_users(self) -> list[dict[str, Any]]:
        """Fetch all users in the workspace."""
        workspace_gid: str = await self._get_workspace_gid()
        result: dict[str, Any] = await self._get(
            f"/workspaces/{workspace_gid}/users",
            {"opt_fields": "gid,name,email", "limit": 100},
        )
        return result.get("data", [])

    # ── Resolve helpers ──────────────────────────────────────────────────

    async def resolve_project_by_name(
        self, project_name: str
    ) -> dict[str, Any] | None:
        """Find a project by name (case-insensitive)."""
        projects: list[dict[str, Any]] = await self.list_projects()
        name_lower: str = project_name.lower().strip()
        for project in projects:
            if project.get("name", "").lower() == name_lower:
                return project
        return None

    async def resolve_assignee_by_name(
        self, name: str
    ) -> dict[str, Any] | None:
        """Find a user by display name (case-insensitive)."""
        users: list[dict[str, Any]] = await self.list_users()
        name_lower: str = name.lower().strip()
        for user in users:
            if user.get("name", "").lower() == name_lower:
                return user
        return None

    # ── CRM no-ops (BaseConnector requires these) ────────────────────────

    async def sync_deals(self) -> int:
        """Not applicable for Asana."""
        return 0

    async def sync_accounts(self) -> int:
        """Not applicable for Asana."""
        return 0

    async def sync_contacts(self) -> int:
        """Not applicable for Asana."""
        return 0

    async def sync_activities(self) -> int:
        """Not applicable for Asana."""
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Not applicable for Asana."""
        raise NotImplementedError("Asana connector does not support deals")

    # ── Override sync_all with Asana-specific flow ───────────────────────

    async def sync_all(self) -> dict[str, int]:
        """
        Run all Asana sync operations.

        Order: teams → projects → tasks (issues).
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
            "tasks": issues_count,
        }
        return result


# ── State mapping helper ──────────────────────────────────────────────────


def _resolve_state_type(is_completed: bool, state_name: str | None) -> str:
    """Map Asana completion status + section name to a normalised state_type."""
    if is_completed:
        return "completed"
    if state_name:
        lower: str = state_name.lower()
        if lower in ("done", "complete", "completed"):
            return "completed"
        if lower in ("backlog", "later", "icebox"):
            return "backlog"
        if lower in ("in progress", "doing", "in review", "started"):
            return "started"
    return "unstarted"


# ── Date parsing helpers ─────────────────────────────────────────────────


def _parse_date(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD date string from Asana."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def _parse_datetime(dt_str: str) -> datetime:
    """Parse an ISO-8601 datetime string from Asana."""
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
