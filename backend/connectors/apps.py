"""
Apps connector – create, read, update, and test interactive mini-apps (React + SQL).

Built-in connector enabled by default for all orgs. Exposes apps as a data source
the agent can query (read) and write (create, update, test_query).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text

from config import settings
from connectors.base import BaseConnector
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorMeta,
    ConnectorScope,
    WriteOperation,
)
from models.app import App
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_session
from models.external_identity_mapping import ExternalIdentityMapping
from models.organization import Organization
from services.public_preview_warmup import warm_public_preview_cache
from services.slack_identity import get_alternate_slack_user_ids_for_identity

logger = logging.getLogger(__name__)

USAGE_GUIDE: str = """# Apps Connector Usage Guide

## Operations
- **create**: Create a new app. Requires title, queries, frontend_code.
- **update**: Update an existing app. Requires app_id, plus queries and/or frontend_code to change.
- **test_query**: Run a query and return sample data to verify correctness. Requires app_id, query_name.

## Recommended workflow
1. Create the app with operation="create"
2. Test queries with operation="test_query" to verify data looks correct
3. If data is wrong, use query "read <app_id>" to inspect, then operation="update" to fix

## App structure
1. **queries** (server-side): Named parameterized SQL queries. Never exposed to browser.
2. **frontend_code** (client-side): React JSX rendered in a sandboxed iframe.

## Available packages in frontend_code
- react, react-dom (hooks: useState, useEffect, useCallback, useMemo, useRef)
- react-plotly.js (import Plot from "react-plotly.js")
- @revtops/app-sdk (useAppQuery, useDateRange, Spinner, ErrorBanner)

## CRITICAL — Data access
The App component receives NO props. Data is NOT passed in. You MUST call `useAppQuery(queryName, params)` inside the component to fetch data from your server-side queries. The returned `data` is an array of rows (objects). Example: `const { data, loading, error } = useAppQuery('my_query', {}); const firstRow = data?.[0];`

## SDK API
- useAppQuery(queryName, params) → { data, columns, loading, error, refetch }
- useDateRange(period) → { start, end } (ISO date strings)
  - period: "last_7d", "last_30d", "last_90d", "last_quarter", "this_quarter", "ytd", "last_year", "this_year"
- Spinner — loading spinner component
- ErrorBanner({ message }) — error display component

## Rules
- All SQL must be SELECT-only. No INSERT/UPDATE/DELETE.
- Do NOT add organization_id to WHERE clauses (RLS handles it).
- frontend_code must export a default React component.

## CRITICAL — Styling rules (apps render inside a sandboxed iframe)
- NO CSS frameworks are available (no Tailwind, no Bootstrap, no CSS modules). Class-based utility styling like className="flex gap-4" will NOT work.
- Use React inline styles for ALL styling: style={{ display: "flex", gap: "1rem" }}
- A minimal dark-theme base is pre-loaded (dark background, light text, basic table/input/button styles). Build on top of it with inline styles.
- Always include layout resets on your root container: style={{ margin: 0, padding: "1rem", width: "100%", maxWidth: "100%", overflowX: "hidden", boxSizing: "border-box" }}
- For reusable style objects, define them as JS constants: const cardStyle = { background: "#27272a", borderRadius: "0.5rem", padding: "1rem", border: "1px solid #3f3f46" };
- Color palette (dark theme): background #18181b, surface #27272a, border #3f3f46, text #e4e4e7, muted text #a1a1aa, accent #6366f1, error #fca5a5.

## Example create (Hello World)
```json
{
  "title": "Hello World",
  "queries": {
    "hello": {
      "sql": "SELECT 'Hello, World!' AS message, NOW() AS current_time",
      "params": {}
    }
  },
  "frontend_code": "import { useAppQuery, Spinner, ErrorBanner } from '@revtops/app-sdk';\\nexport default function App() {\\n  const { data, loading, error } = useAppQuery('hello', {});\\n  if (loading) return <Spinner />;\\n  if (error) return <ErrorBanner message={error.message} />;\\n  const msg = data?.[0]?.message || 'Hello';\\n  const time = data?.[0]?.current_time;\\n  return (\\n    <div style={{ margin: 0, padding: '1rem', minHeight: '200px' }}>\\n      <h1>{msg}</h1>\\n      {time && <p>Server time: {new Date(time).toLocaleString()}</p>}\\n    </div>\\n  );\\n}"
}
```

For Revenue by Region (with params), use useAppQuery('revenue_data', { start_date: '2024-01-01' }) — the query name and params must match the keys in queries.

## Example test_query
```json
{
  "app_id": "abc-123",
  "query_name": "revenue_data",
  "params": { "start_date": "2024-01-01" },
  "limit": 5
}
```

## Example update
```json
{
  "app_id": "abc-123",
  "queries": { "revenue_data": { "sql": "...fixed SQL...", "params": {...} } }
}
```
"""


class AppsConnector(BaseConnector):
    """Create, read, update, and test interactive mini-apps (React + SQL)."""

    source_system: str = "apps"
    meta = ConnectorMeta(
        name="Apps",
        slug="apps",
        auth_type=AuthType.CUSTOM,
        scope=ConnectorScope.ORGANIZATION,
        capabilities=[Capability.QUERY, Capability.WRITE],
        query_description=(
            "Read an app by ID. Use format: 'read <app_id>' where app_id is the UUID. "
            "Returns title, description, queries, and frontend_code so you can inspect before editing."
        ),
        write_operations=[
            WriteOperation(
                name="create",
                entity_type="app",
                description="Create a new app. Requires title, queries, frontend_code.",
                parameters=[
                    {"name": "title", "type": "string", "required": True, "description": "Display title"},
                    {"name": "queries", "type": "object", "required": True, "description": "Named SQL queries. Each key is query name, value is {sql, params}. SQL must be SELECT-only."},
                    {"name": "frontend_code", "type": "string", "required": True, "description": "React JSX code. Must export default component."},
                    {"name": "description", "type": "string", "required": False, "description": "Brief description of the app"},
                ],
            ),
            WriteOperation(
                name="update",
                entity_type="app",
                description="Update an existing app. Requires app_id, plus at least one of queries, frontend_code, title, description.",
                parameters=[
                    {"name": "app_id", "type": "string", "required": True, "description": "UUID of the app to update"},
                    {"name": "queries", "type": "object", "required": False, "description": "New or updated queries"},
                    {"name": "frontend_code", "type": "string", "required": False, "description": "New frontend code"},
                    {"name": "title", "type": "string", "required": False, "description": "New title"},
                    {"name": "description", "type": "string", "required": False, "description": "New description"},
                ],
            ),
            WriteOperation(
                name="test_query",
                entity_type="app",
                description="Run a query from an app and return sample data. Use to verify data before considering the app done.",
                parameters=[
                    {"name": "app_id", "type": "string", "required": True, "description": "UUID of the app"},
                    {"name": "query_name", "type": "string", "required": True, "description": "Name of the query to run"},
                    {"name": "params", "type": "object", "required": False, "description": "Parameter values for the query"},
                    {"name": "limit", "type": "integer", "required": False, "description": "Max rows to return (default 5, max 50)"},
                ],
            ),
        ],
        description="Create and update interactive mini-apps with React + SQL queries.",
        usage_guide=USAGE_GUIDE,
    )

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {}

    async def query(self, request: str) -> dict[str, Any]:
        req: str = (request or "").strip()
        if not req.lower().startswith("read "):
            return {
                "error": "Apps query must use format 'read <app_id>'. Pass the UUID of the app to read."
            }
        app_id_raw: str = req[5:].strip()
        if not app_id_raw:
            return {"error": "app_id is required after 'read '"}
        try:
            app_uuid: UUID = UUID(app_id_raw)
        except ValueError:
            return {"error": "Invalid app_id format (must be a valid UUID)"}

        return await self._read(app_id_raw)

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        if operation == "create":
            return await self._create(data)
        if operation == "update":
            return await self._update(data)
        if operation == "test_query":
            return await self._test_query(data)
        return {"error": f"Unknown operation: {operation}. Use 'create', 'update', or 'test_query'."}

    @staticmethod
    def _validate_queries(queries: dict[str, Any]) -> str | None:
        """Validate queries dict. Returns error message or None if valid."""
        select_re: re.Pattern[str] = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
        dangerous_re: re.Pattern[str] = re.compile(
            r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
            re.IGNORECASE,
        )
        for qname, qspec in queries.items():
            sql: str = qspec.get("sql", "")
            if not sql.strip():
                return f"Query '{qname}' has empty SQL"
            if not select_re.match(sql):
                return f"Query '{qname}' must be a SELECT statement"
            if dangerous_re.search(sql):
                return f"Query '{qname}' contains disallowed SQL keywords"
        return None

    async def _test_execute_queries(self, queries: dict[str, Any]) -> list[str]:
        """Test-execute queries with dummy params. Returns list of errors."""
        errors: list[str] = []
        async with get_session(organization_id=self.organization_id) as session:
            for qname, qspec in queries.items():
                sql: str = qspec.get("sql", "")
                param_defs: dict[str, Any] = qspec.get("params", {})

                test_params: dict[str, Any] = {"org_id": self.organization_id}
                for pname, pdef in param_defs.items():
                    if isinstance(pdef, dict):
                        ptype: str = pdef.get("type", "string")
                    else:
                        ptype = "string"
                    if ptype == "date":
                        test_params[pname] = "2020-01-01"
                    elif ptype == "integer":
                        test_params[pname] = 0
                    elif ptype == "number":
                        test_params[pname] = 0.0
                    else:
                        test_params[pname] = ""

                test_sql: str = f"SELECT * FROM ({sql.rstrip().rstrip(';')}) AS _test LIMIT 0"
                try:
                    await session.execute(text(test_sql), test_params)
                except Exception as exc:
                    errors.append(f"Query '{qname}': {exc}")
        return errors

    @staticmethod
    def _add_line_numbers(code: str) -> str:
        """Add line numbers to code for easier reference when editing."""
        lines: list[str] = code.split("\n")
        width: int = len(str(len(lines)))
        numbered_lines: list[str] = [
            f"{i + 1:>{width}}| {line}" for i, line in enumerate(lines)
        ]
        return "\n".join(numbered_lines)

    async def _read(self, app_id: str) -> dict[str, Any]:
        """Read back an app's queries and frontend_code with line numbers."""
        org_uuid: UUID = UUID(self.organization_id)
        app_uuid: UUID = UUID(app_id)

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(App).where(
                    App.id == app_uuid,
                    App.organization_id == org_uuid,
                )
            )
            app: App | None = result.scalar_one_or_none()
            if not app:
                return {"error": f"App not found: {app_id}"}

            # Materialize all needed fields while the instance is still bound
            # to the active SQLAlchemy session. This avoids DetachedInstanceError
            # when attribute refresh is attempted after context exit.
            title: str = app.title
            description: str | None = app.description
            queries: dict[str, Any] = dict(app.queries or {})
            frontend_code: str = app.frontend_code

        logger.debug(
            "[AppsConnector] Read app payload materialized inside session: app_id=%s query_count=%d",
            app_id,
            len(queries),
        )

        queries_with_line_numbers: dict[str, Any] = {}
        for qname, qspec in queries.items():
            queries_with_line_numbers[qname] = {
                **qspec,
                "sql_with_lines": self._add_line_numbers(qspec.get("sql", "")),
            }

        return {
            "status": "success",
            "app_id": app_id,
            "title": title,
            "description": description,
            "queries": queries_with_line_numbers,
            "frontend_code": frontend_code,
            "frontend_code_with_lines": self._add_line_numbers(frontend_code),
        }

    async def _resolve_user_from_external_actor(
        self,
        *,
        source: str | None,
        external_user_id: str | None,
    ) -> UUID | None:
        """Resolve an internal user from an external actor identifier."""
        normalized_source: str = (source or "").strip().lower()
        normalized_external_user: str = (external_user_id or "").strip().upper()
        if not normalized_source or not normalized_external_user:
            logger.debug(
                "[AppsConnector] External actor resolution skipped due to missing source/external_user_id: source=%s external_user_id=%s",
                source,
                external_user_id,
            )
            return None

        if normalized_source != "slack":
            logger.debug(
                "[AppsConnector] External actor resolution skipped for unsupported source: source=%s external_user_id=%s",
                normalized_source,
                normalized_external_user,
            )
            return None

        org_uuid: UUID = UUID(self.organization_id)
        candidate_external_user_ids: list[str] = [normalized_external_user]
        async with get_session(organization_id=self.organization_id) as session:
            alternate_slack_ids: list[str] = await get_alternate_slack_user_ids_for_identity(
                organization_id=self.organization_id,
                slack_user_id=normalized_external_user,
                session=session,
            )
            for alternate_slack_id in alternate_slack_ids:
                normalized_alternate: str = str(alternate_slack_id).strip().upper()
                if normalized_alternate and normalized_alternate not in candidate_external_user_ids:
                    candidate_external_user_ids.append(normalized_alternate)

            logger.info(
                "[AppsConnector] Attempting external actor owner resolution across Slack identities: source=%s external_user_ids=%s",
                normalized_source,
                candidate_external_user_ids,
            )

            mapping_rows = await session.execute(
                select(
                    ExternalIdentityMapping.external_userid,
                    ExternalIdentityMapping.source,
                    ExternalIdentityMapping.user_id,
                )
                .where(ExternalIdentityMapping.organization_id == org_uuid)
                .where(ExternalIdentityMapping.external_userid.in_(candidate_external_user_ids))
                .where(ExternalIdentityMapping.source.in_(("slack", "revtops_unknown")))
                .where(ExternalIdentityMapping.user_id.is_not(None))
                .order_by(ExternalIdentityMapping.updated_at.desc())
            )
            mappings: list[tuple[str, str, UUID]] = list(mapping_rows.all())
            if mappings:
                selected_external_user_id: str
                selected_source: str
                selected_user_id: UUID
                selected_external_user_id, selected_source, selected_user_id = mappings[0]
                logger.info(
                    "[AppsConnector] Resolved app owner from Slack identity candidates: selected_external_user_id=%s source=%s user_id=%s total_candidate_mappings=%d",
                    selected_external_user_id,
                    selected_source,
                    selected_user_id,
                    len(mappings),
                )
                return selected_user_id

        logger.debug(
            "[AppsConnector] Could not resolve app owner from external actor: source=%s external_user_id=%s",
            normalized_source,
            normalized_external_user,
        )
        return None

    async def _create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new app."""
        title: str = str(data.get("title", "Untitled App"))
        description: str = str(data.get("description", ""))
        queries: dict[str, Any] = data.get("queries", {})
        frontend_code: str = str(data.get("frontend_code", ""))

        if not queries:
            return {"error": "At least one query is required in the 'queries' object"}
        if not frontend_code.strip():
            return {"error": "frontend_code cannot be empty"}

        validation_error: str | None = self._validate_queries(queries)
        if validation_error:
            return {"error": validation_error}

        errors: list[str] = await self._test_execute_queries(queries)
        if errors:
            return {"error": "SQL validation failed:\n" + "\n".join(errors)}

        message_id: str | None = data.get("message_id")
        conversation_id: str | None = data.get("conversation_id")
        user_uuid: UUID | None = None
        conversation_uuid: UUID | None = None
        owner_override_raw: Any = data.get(" app created by")
        owner_override_id: str | None = None
        if owner_override_raw is not None:
            owner_override_id = str(owner_override_raw).strip()
            if not owner_override_id:
                owner_override_id = None

        logger.info(
            "[AppsConnector] Creating app with ownership context: org_id=%s message_id=%s conversation_id=%s connector_user_id=%s owner_override_present=%s",
            self.organization_id,
            message_id,
            conversation_id,
            self.user_id,
            bool(owner_override_id),
        )

        if owner_override_id:
            try:
                user_uuid = UUID(owner_override_id)
                logger.info(
                    "[AppsConnector] Using explicit app owner override from request payload: user_id=%s",
                    user_uuid,
                )
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    "[AppsConnector] Invalid app owner override provided; expected UUID: override_value=%s",
                    owner_override_raw,
                )
                return {"error": "Invalid ' app created by' value: must be a valid UUID string"}

        connector_user_uuid: UUID | None = None
        if self.user_id and user_uuid is None:
            try:
                connector_user_uuid = UUID(self.user_id)
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    "[AppsConnector] Could not parse connector user_id as UUID for owner resolution: user_id=%s",
                    self.user_id,
                )

        if conversation_id and user_uuid is None:
            try:
                conversation_uuid = UUID(conversation_id)
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    "[AppsConnector] Could not parse conversation_id as UUID for owner resolution fallback: conversation_id=%s",
                    conversation_id,
                )
            else:
                async with get_session(organization_id=self.organization_id) as session:
                    row = await session.execute(
                        select(
                            Conversation.user_id,
                            Conversation.source,
                            Conversation.source_user_id,
                        ).where(
                            Conversation.id == conversation_uuid,
                        )
                    )
                    conversation_record: tuple[UUID | None, str | None, str | None] | None = row.one_or_none()
                    conversation_user_id: UUID | None = None
                    conversation_source: str | None = None
                    conversation_source_user_id: str | None = None
                    if conversation_record is not None:
                        (
                            conversation_user_id,
                            conversation_source,
                            conversation_source_user_id,
                        ) = conversation_record
                    if conversation_user_id is not None:
                        user_uuid = conversation_user_id
                        logger.info(
                            "[AppsConnector] Resolved app owner from conversation owner: conversation_id=%s user_id=%s",
                            conversation_id,
                            conversation_user_id,
                        )
                        if connector_user_uuid is not None and connector_user_uuid != conversation_user_id:
                            logger.info(
                                "[AppsConnector] Conversation owner overrides connector user for app owner: conversation_owner_id=%s connector_user_id=%s",
                                conversation_user_id,
                                connector_user_uuid,
                            )
                    else:
                        external_actor_user_id: UUID | None = await self._resolve_user_from_external_actor(
                            source=conversation_source,
                            external_user_id=conversation_source_user_id,
                        )
                        if external_actor_user_id is not None:
                            user_uuid = external_actor_user_id
                            logger.info(
                                "[AppsConnector] Resolved app owner from external actor mapping: conversation_id=%s source=%s external_user_id=%s user_id=%s",
                                conversation_id,
                                conversation_source,
                                conversation_source_user_id,
                                external_actor_user_id,
                            )

        if message_id and user_uuid is None:
            try:
                message_uuid = UUID(message_id)
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    "[AppsConnector] Could not parse message_id as UUID for owner resolution fallback: message_id=%s",
                    message_id,
                )
            else:
                async with get_session(organization_id=self.organization_id) as session:
                    row = await session.execute(
                        select(ChatMessage.user_id).where(
                            ChatMessage.id == message_uuid,
                        )
                    )
                    message_user_id: UUID | None = row.scalar_one_or_none()
                    if message_user_id is not None and user_uuid is None:
                        user_uuid = message_user_id
                        logger.info(
                            "[AppsConnector] Resolved app owner from initiating message: message_id=%s user_id=%s",
                            message_id,
                            message_user_id,
                        )

        if connector_user_uuid is not None and user_uuid is None:
            user_uuid = connector_user_uuid
            logger.info(
                "[AppsConnector] Using current turn user context for app owner fallback: user_id=%s",
                connector_user_uuid,
            )

        if not user_uuid:
            logger.warning(
                "[AppsConnector] App owner unresolved; aborting app creation: org_id=%s message_id=%s conversation_id=%s connector_user_id=%s",
                self.organization_id,
                message_id,
                conversation_id,
                self.user_id,
            )
            return {
                "error": "App creation requires a user context. This can happen in some automated flows; try creating the app from a normal chat message.",
            }

        app_uuid: UUID = uuid4()
        app_id_str: str = str(app_uuid)
        org_uuid: UUID = UUID(self.organization_id)

        async with get_session(organization_id=self.organization_id) as session:
            from utils.transpile_jsx import transpile_jsx

            transpile_result: tuple[str | None, ...] = transpile_jsx(frontend_code)
            compiled_code: str | None = transpile_result[0] if transpile_result else None

            msg_id_str: str | None = str(message_id) if message_id else None

            app: App = App(
                id=app_uuid,
                user_id=user_uuid,
                organization_id=org_uuid,
                title=title,
                description=description,
                queries=queries,
                frontend_code=frontend_code,
                frontend_code_compiled=compiled_code,
                conversation_id=conversation_uuid,
                message_id=msg_id_str,
            )
            session.add(app)
            await session.commit()

        logger.info(
            "[AppsConnector] Created app: id=%s, title=%s, queries=%s",
            app_id_str,
            title,
            list(queries.keys()),
        )
        await warm_public_preview_cache("app", app_id_str)

        app_uri_path: str = await self._build_app_uri_path(app_id_str)
        app_url: str = f"{settings.FRONTEND_URL.rstrip('/')}{app_uri_path}"
        return {
            "status": "success",
            "app_id": app_id_str,
            "app": {
                "id": app_id_str,
                "title": title,
                "description": description,
                "frontendCode": frontend_code,
                "frontendCodeCompiled": compiled_code,
            },
            "uri": app_uri_path,
            "url": app_url,
            "message": f"Created interactive app: {title}. View it at {app_url}",
        }

    async def _update(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing app's queries and/or frontend_code."""
        app_id_raw: str | None = data.get("app_id")
        if not app_id_raw:
            return {"error": "app_id is required for update operation"}

        new_queries: dict[str, Any] | None = data.get("queries")
        new_frontend_code: str | None = data.get("frontend_code")
        new_title: str | None = data.get("title")
        new_description: str | None = data.get("description")

        if not new_queries and new_frontend_code is None and new_title is None and new_description is None:
            return {"error": "At least one of queries, frontend_code, title, or description must be provided for update"}

        org_uuid: UUID = UUID(self.organization_id)
        app_uuid: UUID = UUID(app_id_raw)

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(App).where(
                    App.id == app_uuid,
                    App.organization_id == org_uuid,
                )
            )
            app: App | None = result.scalar_one_or_none()

            if not app:
                return {"error": f"App not found: {app_id_raw}"}

            if new_queries:
                validation_error: str | None = self._validate_queries(new_queries)
                if validation_error:
                    return {"error": validation_error}

                errors: list[str] = await self._test_execute_queries(new_queries)
                if errors:
                    return {"error": "SQL validation failed:\n" + "\n".join(errors)}

                app.queries = new_queries

            if new_frontend_code is not None:
                if not str(new_frontend_code).strip():
                    return {"error": "frontend_code cannot be empty"}
                app.frontend_code = str(new_frontend_code)
                from utils.transpile_jsx import transpile_jsx

                transpile_result = transpile_jsx(app.frontend_code)
                app.frontend_code_compiled = transpile_result[0] if transpile_result else None

            if new_title is not None:
                app.title = str(new_title)

            if new_description is not None:
                app.description = str(new_description)

            await session.commit()

            logger.info("[AppsConnector] Updated app: id=%s, title=%s", app_id_raw, app.title)

        app_uri_path: str = await self._build_app_uri_path(app_id_raw)
        app_url: str = f"{settings.FRONTEND_URL.rstrip('/')}{app_uri_path}"
        return {
            "status": "success",
            "app_id": app_id_raw,
            "app": {
                "id": app_id_raw,
                "title": app.title,
                "description": app.description or "",
                "frontendCode": app.frontend_code,
                "frontendCodeCompiled": app.frontend_code_compiled,
            },
            "uri": app_uri_path,
            "url": app_url,
            "message": f"Updated app: {app.title}. View it at {app_url}",
        }

    async def _build_app_uri_path(self, app_id: str) -> str:
        org_handle: str | None = await self._get_org_handle()
        if org_handle:
            return f"/{org_handle}/apps/{app_id}"
        return f"/apps/{app_id}"

    async def _get_org_handle(self) -> str | None:
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Organization.handle).where(Organization.id == UUID(self.organization_id))
            )
            org_handle: str | None = result.scalar_one_or_none()
        normalized: str = (org_handle or "").strip()
        return normalized or None

    async def _test_query(self, data: dict[str, Any]) -> dict[str, Any]:
        """Run a query from an app and return sample data."""
        app_id_raw: str | None = data.get("app_id")
        query_name: str | None = data.get("query_name")
        query_params: dict[str, Any] = data.get("params", {})
        limit: int = min(data.get("limit", 5), 50)

        if not app_id_raw:
            return {"error": "app_id is required for test_query operation"}
        if not query_name:
            return {"error": "query_name is required for test_query operation"}

        org_uuid: UUID = UUID(self.organization_id)
        app_uuid: UUID = UUID(app_id_raw)

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(App).where(
                    App.id == app_uuid,
                    App.organization_id == org_uuid,
                )
            )
            app: App | None = result.scalar_one_or_none()

            if not app:
                return {"error": f"App not found: {app_id_raw}"}

            queries: dict[str, Any] = app.queries
            if query_name not in queries:
                return {
                    "error": f"Query '{query_name}' not found in app. Available queries: {list(queries.keys())}"
                }

            query_spec: dict[str, Any] = queries[query_name]
            sql: str = query_spec.get("sql", "")

            exec_params: dict[str, Any] = {"org_id": self.organization_id, **query_params}

            limited_sql: str = f"SELECT * FROM ({sql.rstrip().rstrip(';')}) AS _q LIMIT {limit}"

            try:
                result = await session.execute(text(limited_sql), exec_params)
                rows: list[dict[str, Any]] = [dict(row._mapping) for row in result.fetchall()]

                for row in rows:
                    for key, value in list(row.items()):
                        if isinstance(value, UUID):
                            row[key] = str(value)
                        elif hasattr(value, "isoformat"):
                            row[key] = value.isoformat()

                return {
                    "status": "success",
                    "app_id": app_id_raw,
                    "query_name": query_name,
                    "row_count": len(rows),
                    "limit": limit,
                    "data": rows,
                }
            except Exception as exc:
                return {
                    "error": f"Query execution failed: {exc}",
                    "query_name": query_name,
                    "sql": sql,
                }
