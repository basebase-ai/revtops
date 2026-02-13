"""
Linear connector – syncs teams, projects, and issues via the GraphQL API.

Unlike CRM connectors, Linear data doesn't map to accounts/deals/contacts.
The CRM abstract methods are implemented as no-ops; sync_all() is overridden
to run Linear-specific sync operations instead.

OAuth is handled through Nango (Linear OAuth App).
Linear API docs: https://developers.linear.app/docs
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
from models.database import get_session
from models.tracker_issue import TrackerIssue
from models.tracker_project import TrackerProject
from models.tracker_team import TrackerTeam

logger = logging.getLogger(__name__)

LINEAR_API_URL: str = "https://api.linear.app/graphql"

# ── Priority mapping (Linear uses 0-4) ──────────────────────────────────
PRIORITY_LABELS: dict[int, str] = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}


class LinearConnector(BaseConnector):
    """Connector for Linear – teams, projects, and issues."""

    source_system: str = "linear"

    def __init__(
        self, organization_id: str, user_id: Optional[str] = None
    ) -> None:
        super().__init__(organization_id, user_id)

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

    # ── Write: Create Issue ──────────────────────────────────────────────

    async def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str | None = None,
        priority: int | None = None,
        assignee_id: str | None = None,
        project_id: str | None = None,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an issue in Linear via the issueCreate mutation."""
        variables: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
        }
        if description:
            variables["description"] = description
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
        issue_id: str,
        title: str | None = None,
        description: str | None = None,
        state_id: str | None = None,
        priority: int | None = None,
        assignee_id: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing issue in Linear via the issueUpdate mutation."""
        input_fields: dict[str, Any] = {}
        if title is not None:
            input_fields["title"] = title
        if description is not None:
            input_fields["description"] = description
        if state_id is not None:
            input_fields["stateId"] = state_id
        if priority is not None:
            input_fields["priority"] = priority
        if assignee_id is not None:
            input_fields["assigneeId"] = assignee_id

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
        """Find a workflow state by name within a team."""
        states: list[dict[str, Any]] = await self.list_workflow_states(team_id)
        name_lower: str = state_name.lower().strip()
        for state in states:
            if state["name"].lower() == name_lower:
                return state
        return None

    async def resolve_assignee_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a user by display name (case-insensitive)."""
        data: dict[str, Any] = await self._gql("""
        query {
            users {
                nodes {
                    id
                    name
                    email
                }
            }
        }
        """)
        users: list[dict[str, Any]] = data.get("users", {}).get("nodes", [])
        name_lower: str = name.lower().strip()
        for user in users:
            if user["name"].lower() == name_lower:
                return user
        return None

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
