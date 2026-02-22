"""
Jira connector – syncs projects and issues via the REST API.

Like the Linear connector, Jira data maps to tracker_projects / tracker_issues
rather than CRM objects. The CRM abstract methods are implemented as no-ops;
sync_all() is overridden to run Jira-specific sync operations.

OAuth is handled through Nango (Atlassian/Jira OAuth App).
Jira Cloud REST API docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
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

JIRA_ISSUE_DONE_EVENT: str = "jira.issue.done"

PRIORITY_MAP: dict[str, tuple[int, str]] = {
    "highest": (1, "Highest"),
    "high": (2, "High"),
    "medium": (3, "Medium"),
    "low": (4, "Low"),
    "lowest": (5, "Lowest"),
}


class JiraConnector(BaseConnector):
    """Connector for Jira – projects and issues."""

    source_system: str = "jira"
    meta = ConnectorMeta(
        name="Jira",
        slug="jira",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["projects", "issues"],
        capabilities=[Capability.SYNC, Capability.WRITE, Capability.LISTEN],
        write_operations=[
            WriteOperation(
                name="create_issue", entity_type="issue",
                description="Create a Jira issue",
                parameters=[
                    {"name": "project_key", "type": "string", "required": True, "description": "Project key (e.g. 'ENG')"},
                    {"name": "summary", "type": "string", "required": True, "description": "Issue summary/title"},
                    {"name": "description", "type": "string", "required": False, "description": "Issue description"},
                    {"name": "issue_type", "type": "string", "required": False, "description": "Issue type (Task, Bug, Story, etc.). Defaults to Task."},
                    {"name": "priority", "type": "string", "required": False, "description": "Priority (Highest, High, Medium, Low, Lowest)"},
                    {"name": "assignee_name", "type": "string", "required": False, "description": "Assignee display name"},
                    {"name": "labels", "type": "array", "required": False, "description": "Label names to add"},
                ],
            ),
            WriteOperation(
                name="update_issue", entity_type="issue",
                description="Update an existing Jira issue",
                parameters=[
                    {"name": "issue_key", "type": "string", "required": True, "description": "Issue key (e.g. 'ENG-123')"},
                    {"name": "summary", "type": "string", "required": False, "description": "New summary/title"},
                    {"name": "description", "type": "string", "required": False, "description": "New description"},
                    {"name": "status_name", "type": "string", "required": False, "description": "New status name (triggers transition)"},
                    {"name": "priority", "type": "string", "required": False, "description": "New priority"},
                    {"name": "assignee_name", "type": "string", "required": False, "description": "Assignee display name"},
                ],
            ),
        ],
        nango_integration_id="jira",
        description="Jira – project and issue tracking",
        webhook_secret_extra_data_key="jira_webhook_secret",
    )

    def __init__(
        self, organization_id: str, user_id: Optional[str] = None
    ) -> None:
        super().__init__(organization_id, user_id)
        self._cloud_id: Optional[str] = None
        self._base_url: Optional[str] = None

    # ── REST helpers ─────────────────────────────────────────────────────

    async def _get_headers(self) -> dict[str, str]:
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _get_cloud_id(self) -> str:
        """Resolve and cache the Jira Cloud ID (required for API calls)."""
        if self._cloud_id:
            return self._cloud_id

        headers: dict[str, str] = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers=headers,
            )
            resp.raise_for_status()
            resources: list[dict[str, Any]] = resp.json()

        if not resources:
            raise RuntimeError("No Jira Cloud sites found for this account")

        self._cloud_id = resources[0]["id"]
        self._base_url = f"https://api.atlassian.com/ex/jira/{self._cloud_id}/rest/api/3"
        return self._cloud_id

    async def _get_base_url(self) -> str:
        """Get the base URL for API calls."""
        if not self._base_url:
            await self._get_cloud_id()
        assert self._base_url is not None
        return self._base_url

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GET request against the Jira REST API."""
        headers: dict[str, str] = await self._get_headers()
        base_url: str = await self._get_base_url()
        url: str = f"{base_url}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.get(
                url,
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def _post(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a POST request against the Jira REST API."""
        headers: dict[str, str] = await self._get_headers()
        base_url: str = await self._get_base_url()
        url: str = f"{base_url}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.post(
                url,
                headers=headers,
                json=json_body,
            )
            resp.raise_for_status()
            if resp.status_code == 204:
                return {}
            return resp.json()

    async def _put(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a PUT request against the Jira REST API."""
        headers: dict[str, str] = await self._get_headers()
        base_url: str = await self._get_base_url()
        url: str = f"{base_url}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.put(
                url,
                headers=headers,
                json=json_body,
            )
            resp.raise_for_status()
            if resp.status_code == 204:
                return {}
            return resp.json()

    async def _get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        results_key: str = "values",
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Paginate through a Jira collection (offset-based).

        Jira returns:
          { values: [...], startAt: N, maxResults: M, total: T, isLast: bool }
        or for search:
          { issues: [...], startAt: N, maxResults: M, total: T }
        """
        all_items: list[dict[str, Any]] = []
        current_params: dict[str, Any] = dict(params or {})
        current_params.setdefault("maxResults", 100)
        start_at: int = 0

        for _ in range(max_pages):
            current_params["startAt"] = start_at
            result: dict[str, Any] = await self._get(path, current_params)
            items: list[dict[str, Any]] = result.get(results_key, [])
            if not items:
                break
            all_items.extend(items)

            total: int = result.get("total", 0)
            if start_at + len(items) >= total:
                break
            if result.get("isLast", False):
                break

            start_at += len(items)

        return all_items

    # ── Integration helper ───────────────────────────────────────────────

    async def _get_integration(self) -> Any:
        """Load the Integration record (cached on self._integration)."""
        if self._integration:
            return self._integration
        await self.get_oauth_token()
        assert self._integration is not None
        return self._integration

    # ── Sync: Projects ───────────────────────────────────────────────────

    async def sync_projects(self) -> int:
        """Fetch all projects from Jira and upsert into tracker_projects."""
        org_uuid: UUID = UUID(self.organization_id)
        integration = await self._get_integration()
        integration_id: UUID = integration.id

        projects: list[dict[str, Any]] = await self._get_paginated(
            "/project/search",
            {"expand": "description,lead"},
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for p in projects:
                lead: dict[str, Any] | None = p.get("lead")

                # Create a "team" entry for each project (Jira doesn't have teams like Linear)
                team_stmt = pg_insert(TrackerTeam).values(
                    organization_id=org_uuid,
                    integration_id=integration_id,
                    source_system="jira",
                    source_id=f"project-{p['id']}",
                    name=p.get("name", ""),
                    key=p.get("key"),
                    description=None,
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "name": p.get("name", ""),
                        "key": p.get("key"),
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(team_stmt)

                stmt = pg_insert(TrackerProject).values(
                    organization_id=org_uuid,
                    source_system="jira",
                    source_id=p["id"],
                    name=p.get("name", ""),
                    description=p.get("description"),
                    state="active" if not p.get("archived", False) else "archived",
                    progress=None,
                    target_date=None,
                    start_date=None,
                    url=p.get("self", ""),
                    lead_name=lead.get("displayName") if lead else None,
                    team_ids=[f"project-{p['id']}"],
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "name": p.get("name", ""),
                        "description": p.get("description"),
                        "state": "active" if not p.get("archived", False) else "archived",
                        "lead_name": lead.get("displayName") if lead else None,
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)
                count += 1
            await session.commit()

        logger.info("Synced %d Jira projects for org %s", count, self.organization_id)
        return count

    # ── Sync: Issues ─────────────────────────────────────────────────────

    async def sync_issues(self) -> int:
        """Fetch issues from Jira and upsert into tracker_issues."""
        org_uuid: UUID = UUID(self.organization_id)

        # Build lookup of source_id → internal UUID for teams (projects)
        team_map: dict[str, UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(TrackerTeam.source_id, TrackerTeam.id).where(
                    TrackerTeam.organization_id == org_uuid,
                    TrackerTeam.source_system == "jira",
                )
            )
            for row in result.all():
                team_map[row[0]] = row[1]

        # Build lookup of source_id → internal UUID for projects
        project_map: dict[str, UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(TrackerProject.source_id, TrackerProject.id).where(
                    TrackerProject.organization_id == org_uuid,
                    TrackerProject.source_system == "jira",
                )
            )
            for row in result.all():
                project_map[row[0]] = row[1]

        # Fetch issues using JQL search
        jql: str = "ORDER BY updated DESC"
        issues: list[dict[str, Any]] = await self._get_paginated(
            "/search",
            {
                "jql": jql,
                "fields": (
                    "summary,description,status,priority,assignee,reporter,"
                    "project,labels,timeestimate,created,updated,resolutiondate,"
                    "issuetype,resolution"
                ),
            },
            results_key="issues",
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for issue in issues:
                fields: dict[str, Any] = issue.get("fields", {})
                project_data: dict[str, Any] | None = fields.get("project")
                if not project_data:
                    continue

                project_id: str = project_data["id"]
                team_source_id: str = f"project-{project_id}"
                internal_team_id: UUID | None = team_map.get(team_source_id)
                if not internal_team_id:
                    continue

                internal_project_id: UUID | None = project_map.get(project_id)

                status_data: dict[str, Any] | None = fields.get("status")
                priority_data: dict[str, Any] | None = fields.get("priority")
                assignee: dict[str, Any] | None = fields.get("assignee")
                reporter: dict[str, Any] | None = fields.get("reporter")
                issue_type: dict[str, Any] | None = fields.get("issuetype")
                resolution: dict[str, Any] | None = fields.get("resolution")
                labels: list[str] = fields.get("labels", [])

                # Map status category to state_type
                state_type: str = "unstarted"
                if status_data:
                    category: str | None = status_data.get("statusCategory", {}).get("key")
                    if category == "done":
                        state_type = "completed"
                    elif category == "indeterminate":
                        state_type = "started"

                # Map priority
                priority: int | None = None
                priority_label: str | None = None
                if priority_data and priority_data.get("name"):
                    pname: str = priority_data["name"].lower()
                    mapped: tuple[int, str] | None = PRIORITY_MAP.get(pname)
                    if mapped:
                        priority, priority_label = mapped
                    else:
                        priority_label = priority_data["name"]

                # Parse description (Jira uses ADF format, extract text)
                description_text: str | None = None
                desc_field: Any = fields.get("description")
                if desc_field:
                    if isinstance(desc_field, str):
                        description_text = desc_field
                    elif isinstance(desc_field, dict):
                        description_text = _extract_adf_text(desc_field)

                # Estimate in seconds → hours
                estimate: float | None = None
                time_estimate: int | None = fields.get("timeestimate")
                if time_estimate:
                    estimate = time_estimate / 3600.0

                stmt = pg_insert(TrackerIssue).values(
                    organization_id=org_uuid,
                    team_id=internal_team_id,
                    source_system="jira",
                    source_id=issue["id"],
                    identifier=issue["key"],
                    title=fields.get("summary", ""),
                    description=description_text[:5000] if description_text else None,
                    state_name=status_data["name"] if status_data else None,
                    state_type=state_type,
                    priority=priority,
                    priority_label=priority_label,
                    issue_type=issue_type["name"] if issue_type else None,
                    assignee_name=assignee["displayName"] if assignee else None,
                    assignee_email=assignee.get("emailAddress") if assignee else None,
                    creator_name=reporter["displayName"] if reporter else None,
                    project_id=internal_project_id,
                    labels=labels or None,
                    estimate=estimate,
                    url=f"{issue.get('self', '').rsplit('/rest/', 1)[0]}/browse/{issue['key']}",
                    due_date=_parse_date(fields.get("duedate")),
                    created_date=_parse_datetime(fields["created"]),
                    updated_date=_parse_datetime_optional(fields.get("updated")),
                    completed_date=_parse_datetime_optional(fields.get("resolutiondate")),
                    cancelled_date=None,
                ).on_conflict_do_update(
                    index_elements=["organization_id", "source_system", "source_id"],
                    set_={
                        "team_id": internal_team_id,
                        "identifier": issue["key"],
                        "title": fields.get("summary", ""),
                        "description": description_text[:5000] if description_text else None,
                        "state_name": status_data["name"] if status_data else None,
                        "state_type": state_type,
                        "priority": priority,
                        "priority_label": priority_label,
                        "issue_type": issue_type["name"] if issue_type else None,
                        "assignee_name": assignee["displayName"] if assignee else None,
                        "assignee_email": assignee.get("emailAddress") if assignee else None,
                        "creator_name": reporter["displayName"] if reporter else None,
                        "project_id": internal_project_id,
                        "labels": labels or None,
                        "estimate": estimate,
                        "due_date": _parse_date(fields.get("duedate")),
                        "updated_date": _parse_datetime_optional(fields.get("updated")),
                        "completed_date": _parse_datetime_optional(fields.get("resolutiondate")),
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)
                count += 1
            await session.commit()

        logger.info("Synced %d Jira issues for org %s", count, self.organization_id)
        return count

    # ── Write: Dispatch ─────────────────────────────────────────────────

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a record-level write operation."""
        if operation == "create_issue":
            return await self.create_issue(**data)
        if operation == "update_issue":
            return await self.update_issue(**data)
        raise ValueError(f"Unknown write operation: {operation}")

    # ── Write: Create Issue ──────────────────────────────────────────────

    async def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description: str | None = None,
        issue_type: str = "Task",
        priority: str | None = None,
        assignee_id: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an issue in Jira."""
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }

        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }

        if priority:
            fields["priority"] = {"name": priority}

        if assignee_id:
            fields["assignee"] = {"accountId": assignee_id}

        if labels:
            fields["labels"] = labels

        result: dict[str, Any] = await self._post("/issue", {"fields": fields})

        return {
            "jira_issue_id": result.get("id"),
            "key": result.get("key"),
            "url": result.get("self", ""),
        }

    # ── Write: Update Issue ──────────────────────────────────────────────

    async def update_issue(
        self,
        *,
        issue_key: str,
        summary: str | None = None,
        description: str | None = None,
        status_name: str | None = None,
        priority: str | None = None,
        assignee_id: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing issue in Jira."""
        fields: dict[str, Any] = {}

        if summary is not None:
            fields["summary"] = summary

        if description is not None:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }

        if priority is not None:
            fields["priority"] = {"name": priority}

        if assignee_id is not None:
            fields["assignee"] = {"accountId": assignee_id}

        if fields:
            await self._put(f"/issue/{issue_key}", {"fields": fields})

        # Handle status transition separately
        if status_name:
            await self._transition_issue(issue_key, status_name)

        # Fetch updated issue
        updated: dict[str, Any] = await self._get(f"/issue/{issue_key}")
        updated_fields: dict[str, Any] = updated.get("fields", {})
        status_data: dict[str, Any] | None = updated_fields.get("status")

        return {
            "jira_issue_id": updated.get("id"),
            "key": updated.get("key"),
            "summary": updated_fields.get("summary"),
            "status": status_data["name"] if status_data else None,
        }

    async def _transition_issue(self, issue_key: str, status_name: str) -> None:
        """Transition an issue to a new status."""
        transitions: dict[str, Any] = await self._get(f"/issue/{issue_key}/transitions")
        available: list[dict[str, Any]] = transitions.get("transitions", [])

        target_name_lower: str = status_name.lower().strip()
        for t in available:
            if t.get("name", "").lower() == target_name_lower:
                await self._post(
                    f"/issue/{issue_key}/transitions",
                    {"transition": {"id": t["id"]}},
                )
                return

        available_names: list[str] = [t.get("name", "") for t in available]
        raise ValueError(
            f"Status '{status_name}' not available. Available: {available_names}"
        )

    # ── Read: Search Issues ──────────────────────────────────────────────

    async def search_issues(
        self,
        *,
        query_text: str,
        project_key: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search issues in Jira using JQL."""
        jql_parts: list[str] = [f'text ~ "{query_text}"']
        if project_key:
            jql_parts.append(f'project = "{project_key}"')
        jql: str = " AND ".join(jql_parts) + " ORDER BY updated DESC"

        result: dict[str, Any] = await self._get(
            "/search",
            {
                "jql": jql,
                "maxResults": min(limit, 50),
                "fields": "summary,description,status,priority,assignee,project,labels,created,updated",
            },
        )
        issues: list[dict[str, Any]] = result.get("issues", [])

        results: list[dict[str, Any]] = []
        for issue in issues:
            fields: dict[str, Any] = issue.get("fields", {})
            status: dict[str, Any] | None = fields.get("status")
            priority_data: dict[str, Any] | None = fields.get("priority")
            assignee: dict[str, Any] | None = fields.get("assignee")
            project: dict[str, Any] | None = fields.get("project")

            desc_text: str | None = None
            desc_field: Any = fields.get("description")
            if desc_field:
                if isinstance(desc_field, str):
                    desc_text = desc_field[:500]
                elif isinstance(desc_field, dict):
                    desc_text = (_extract_adf_text(desc_field) or "")[:500]

            results.append({
                "key": issue["key"],
                "summary": fields.get("summary"),
                "description": desc_text,
                "url": f"{issue.get('self', '').rsplit('/rest/', 1)[0]}/browse/{issue['key']}",
                "status": status["name"] if status else None,
                "status_category": status.get("statusCategory", {}).get("name") if status else None,
                "priority": priority_data["name"] if priority_data else None,
                "assignee": assignee["displayName"] if assignee else None,
                "project": f"{project['key']} ({project['name']})" if project else None,
                "labels": fields.get("labels", []),
                "created_at": fields.get("created"),
                "updated_at": fields.get("updated"),
            })

        return results

    # ── Read: List Projects ──────────────────────────────────────────────

    async def list_projects(self) -> list[dict[str, Any]]:
        """Quick query to list all projects."""
        result: dict[str, Any] = await self._get(
            "/project/search",
            {"maxResults": 100},
        )
        return result.get("values", [])

    # ── Read: List Statuses ──────────────────────────────────────────────

    async def list_statuses(self, project_key: str) -> list[dict[str, Any]]:
        """Fetch all statuses available for a project."""
        result: list[dict[str, Any]] = await self._get(f"/project/{project_key}/statuses")
        statuses: list[dict[str, Any]] = []
        for issue_type in result:
            for status in issue_type.get("statuses", []):
                if status not in statuses:
                    statuses.append({
                        "id": status["id"],
                        "name": status["name"],
                        "category": status.get("statusCategory", {}).get("name"),
                    })
        return statuses

    # ── Read: List Users ─────────────────────────────────────────────────

    async def list_users(self, project_key: str | None = None) -> list[dict[str, Any]]:
        """Fetch users assignable to issues."""
        params: dict[str, Any] = {"maxResults": 100}
        if project_key:
            params["project"] = project_key

        result: list[dict[str, Any]] = await self._get("/user/assignable/search", params)
        return [
            {
                "accountId": u.get("accountId"),
                "displayName": u.get("displayName"),
                "emailAddress": u.get("emailAddress"),
            }
            for u in result
        ]

    # ── Resolve helpers ──────────────────────────────────────────────────

    async def resolve_project_by_key(self, project_key: str) -> dict[str, Any] | None:
        """Find a project by its key."""
        projects: list[dict[str, Any]] = await self.list_projects()
        key_upper: str = project_key.upper().strip()
        for project in projects:
            if project.get("key", "").upper() == key_upper:
                return project
        return None

    async def resolve_assignee_by_name(
        self, name: str, project_key: str | None = None
    ) -> dict[str, Any] | None:
        """Find a user by display name (case-insensitive)."""
        users: list[dict[str, Any]] = await self.list_users(project_key)
        name_lower: str = name.lower().strip()
        for user in users:
            if user.get("displayName", "").lower() == name_lower:
                return user
        return None

    # ── LISTEN: Inbound webhooks ─────────────────────────────────────────

    @staticmethod
    def verify_webhook(raw_body: bytes, headers: dict[str, str], secret: str) -> bool:
        """Verify Jira webhook signature (X-Hub-Signature header)."""
        signature_header: str | None = (
            headers.get("x-hub-signature") or headers.get("X-Hub-Signature")
        )
        if not signature_header or not secret:
            return False
        try:
            if signature_header.startswith("sha256="):
                signature_header = signature_header[7:]
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
        Parse Jira webhook JSON; return [(event_type, data), ...] for workflow events.
        Emits jira.issue.done when an issue is moved to Done status category.
        """
        events: list[tuple[str, dict[str, Any]]] = []

        webhook_event: str | None = payload.get("webhookEvent")
        if webhook_event not in ("jira:issue_updated", "jira:issue_created"):
            return events

        issue: dict[str, Any] | None = payload.get("issue")
        if not issue:
            return events

        fields: dict[str, Any] = issue.get("fields", {})
        status: dict[str, Any] | None = fields.get("status")
        if not status:
            return events

        category: str | None = status.get("statusCategory", {}).get("key")
        if category != "done":
            return events

        # Check if this was a transition to done
        changelog: dict[str, Any] | None = payload.get("changelog")
        if changelog:
            for item in changelog.get("items", []):
                if item.get("field") == "status":
                    to_category: str | None = item.get("to")
                    # Only emit if transitioning TO done
                    if to_category:
                        events.append(
                            (
                                JIRA_ISSUE_DONE_EVENT,
                                {
                                    "webhookEvent": webhook_event,
                                    "timestamp": payload.get("timestamp"),
                                    "issue": issue,
                                    "changelog": changelog,
                                },
                            )
                        )
                        break
        elif webhook_event == "jira:issue_created" and category == "done":
            events.append(
                (
                    JIRA_ISSUE_DONE_EVENT,
                    {
                        "webhookEvent": webhook_event,
                        "timestamp": payload.get("timestamp"),
                        "issue": issue,
                    },
                )
            )

        return events

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Handle an inbound Jira webhook event (LISTEN capability)."""
        if event_type == JIRA_ISSUE_DONE_EVENT:
            issue: dict[str, Any] = payload.get("issue", {})
            logger.info(
                "[jira] Received issue.done for org %s: %s",
                self.organization_id,
                issue.get("key"),
            )

    # ── CRM no-ops (BaseConnector requires these) ────────────────────────

    async def sync_deals(self) -> int:
        """Not applicable for Jira."""
        return 0

    async def sync_accounts(self) -> int:
        """Not applicable for Jira."""
        return 0

    async def sync_contacts(self) -> int:
        """Not applicable for Jira."""
        return 0

    async def sync_activities(self) -> int:
        """Not applicable for Jira."""
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Not applicable for Jira."""
        raise NotImplementedError("Jira connector does not support deals")

    # ── Override sync_all with Jira-specific flow ────────────────────────

    async def sync_all(self) -> dict[str, int]:
        """
        Run all Jira sync operations.

        Order: projects (which creates teams) → issues.
        """
        await self.ensure_sync_active("sync_all:start")

        projects_count: int = await self.sync_projects()
        await self.ensure_sync_active("sync_all:after_projects")

        issues_count: int = await self.sync_issues()

        result: dict[str, int] = {
            "projects": projects_count,
            "issues": issues_count,
        }
        return result


# ── ADF text extraction helper ────────────────────────────────────────────


def _extract_adf_text(adf: dict[str, Any]) -> str | None:
    """Extract plain text from Atlassian Document Format (ADF)."""
    if not adf:
        return None

    texts: list[str] = []

    def _walk(node: dict[str, Any] | list[Any] | str) -> None:
        if isinstance(node, str):
            texts.append(node)
        elif isinstance(node, dict):
            if node.get("type") == "text" and "text" in node:
                texts.append(node["text"])
            for child in node.get("content", []):
                _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(adf)
    return " ".join(texts) if texts else None


# ── Date parsing helpers ─────────────────────────────────────────────────


def _parse_date(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD date string from Jira."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


def _parse_datetime(dt_str: str) -> datetime:
    """Parse an ISO-8601 datetime string from Jira."""
    if not dt_str:
        return datetime.utcnow()
    try:
        cleaned: str = dt_str.replace("Z", "+00:00")
        if "+" not in cleaned and "-" not in cleaned[10:]:
            cleaned = cleaned[:19]
        return datetime.fromisoformat(cleaned).replace(tzinfo=None)
    except (ValueError, TypeError):
        return datetime.utcnow()


def _parse_datetime_optional(dt_str: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime that may be None."""
    if not dt_str:
        return None
    try:
        cleaned: str = dt_str.replace("Z", "+00:00")
        if "+" not in cleaned and "-" not in cleaned[10:]:
            cleaned = cleaned[:19]
        return datetime.fromisoformat(cleaned).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None
