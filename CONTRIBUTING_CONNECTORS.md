# Building a Connector for Revtops

This guide walks you through building a new data source connector. Connectors
let Revtops pull data from external systems, query them on demand, write
records back, execute side-effect actions, and receive real-time events.

## Quick Start

1. Copy the template and name the file to match your connector slug (e.g. `web_search.py` for `slug="web_search"`):
   ```bash
   cp backend/connectors/_template.py backend/connectors/my_crm.py
   ```
2. Update `ConnectorMeta` with your connector's identity and capabilities.
3. Implement the methods for each capability you declared.
4. Test locally (see [Testing](#testing)).
5. Submit a PR.

---

## Architecture Overview

Every connector inherits from `BaseConnector` (in `backend/connectors/base.py`)
and declares a class-level `meta` attribute using `ConnectorMeta`. The system
auto-discovers connectors by scanning `backend/connectors/` at startup — no
manual registration required.

```
backend/connectors/
├── base.py            # BaseConnector ABC
├── registry.py        # ConnectorMeta, Capability, discover_connectors()
├── models.py          # Pydantic record models (DealRecord, etc.)
├── persistence.py     # Sync engine upsert logic
├── _template.py       # Starter template — copy this
├── hubspot.py         # Example: full CRM connector
├── linear.py          # Example: issue tracker with write-back
├── google_drive.py    # Example: file storage with on-demand query
└── ...
```

### How the agent interacts with connectors

The agent has 4 generic tools that work across **all** connectors:

| Tool                     | Capability | Description                        |
| ------------------------ | ---------- | ---------------------------------- |
| `list_connected_systems` | —          | Returns a manifest of all systems  |
| `query_system`           | QUERY      | On-demand data retrieval           |
| `write_to_system`        | WRITE      | Create/update records              |
| `run_action`             | ACTION     | Execute side-effects               |

New connectors automatically appear in the agent's manifest once installed.

---

## Capabilities

Declare which capabilities your connector supports in `ConnectorMeta.capabilities`:

| Capability | What it does                              | Connector method(s)                  |
| ---------- | ----------------------------------------- | ------------------------------------ |
| `SYNC`     | Pull data on a schedule into the warehouse| `sync_deals()`, `sync_accounts()`, etc. or override `sync_all()` |
| `QUERY`    | On-demand data retrieval                  | `get_schema()`, `query(request)`     |
| `WRITE`    | CRUD on records (idempotent)              | `write(operation, data)`             |
| `ACTION`   | Side-effects (not idempotent)             | `execute_action(action, params)`     |
| `LISTEN`   | Receive inbound webhooks/events           | `handle_event(event_type, payload)`  |

You only implement the methods for capabilities you declare. Everything else
raises `NotImplementedError` by default.

### WRITE vs ACTION

- **WRITE** targets a record. Create it, update it, delete it. Idempotent — safe to retry.
  Example: `update_deal(deal_id, {amount: 50000})`
- **ACTION** triggers a side-effect. Not idempotent — should not be blindly retried.
  Example: `send_message(channel="#sales", text="Deal closed!")`

---

## Recipes

### Recipe 1: Basic CRM Sync Connector (Tier 1)

Syncs deals, contacts, accounts to existing canonical tables. No migrations needed.

```python
from connectors.base import BaseConnector
from connectors.models import AccountRecord, ContactRecord, DealRecord
from connectors.registry import AuthType, Capability, ConnectorMeta, ConnectorScope

class PipedriveConnector(BaseConnector):
    source_system = "pipedrive"
    meta = ConnectorMeta(
        name="Pipedrive",
        slug="pipedrive",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["deals", "accounts", "contacts"],
        capabilities=[Capability.SYNC],
        nango_integration_id="pipedrive",
        description="Pipedrive CRM",
    )

    async def sync_deals(self) -> list[DealRecord]:
        token, _ = await self.get_oauth_token()
        # Fetch from Pipedrive API ...
        return [DealRecord(source_id="123", name="Big Deal", amount=50000, source_system=self.source_system)]

    async def sync_accounts(self) -> list[AccountRecord]:
        return []

    async def sync_contacts(self) -> list[ContactRecord]:
        return []

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict:
        return {}
```

The sync engine upserts the returned `DealRecord` objects into the `deals`
table automatically via `connectors/persistence.py`.

### Recipe 2: Issue Tracker Using Shared Tables (Tier 2)

Override `sync_all()` and write to the shared `tracker_*` tables using
`TrackerIssueRecord`, etc.

See `backend/connectors/linear.py` or `backend/connectors/asana.py` for
full working examples. Use `source_system` as the discriminator.

### Recipe 3: New Entity Type (Tier 3)

When your data doesn't fit any existing table, include in your PR:

1. A SQLAlchemy model in `backend/models/my_entity.py`
2. An Alembic migration: `alembic revision --autogenerate -m "add my_entity table"`
3. RLS policies (follow the pattern in `backend/db/migrations/versions/014_add_row_level_security.py`)
4. A Pydantic record model in `backend/connectors/models.py`

The table **must** include `organization_id` with an RLS policy.

### Recipe 4: Query-Only Connector (No Sync)

Lets the agent query a remote system directly. No data stored locally.

```python
from connectors.base import BaseConnector
from connectors.registry import AuthField, AuthType, Capability, ConnectorMeta, ConnectorScope

class CustomerPostgresConnector(BaseConnector):
    source_system = "customer_postgres"
    meta = ConnectorMeta(
        name="Customer Postgres",
        slug="customer_postgres",
        auth_type=AuthType.CUSTOM,
        scope=ConnectorScope.ORGANIZATION,
        capabilities=[Capability.QUERY],
        entity_types=[],
        query_description="SQL (SELECT only). Use get_schema to see tables.",
        auth_fields=[
            AuthField(name="host", label="Database Host"),
            AuthField(name="port", label="Port"),
            AuthField(name="database", label="Database Name"),
            AuthField(name="username", label="Username"),
            AuthField(name="password", label="Password", type="password"),
        ],
    )

    async def get_schema(self) -> list[dict]:
        # Connect and return information_schema metadata
        ...

    async def query(self, request: str) -> dict:
        # Execute read-only SQL and return rows
        ...

    # No sync_* methods needed — they default to returning 0/empty
    async def sync_deals(self) -> int:
        return 0
    async def sync_accounts(self) -> int:
        return 0
    async def sync_contacts(self) -> int:
        return 0
    async def sync_activities(self) -> int:
        return 0
    async def fetch_deal(self, deal_id: str) -> dict:
        return {}
```

### Recipe 5: Connector with Write-Back

Declare `write_operations` in meta (with **parameters** so the agent knows what to send) and implement the `write()` dispatch.

```python
from connectors.registry import Capability, WriteOperation

class MyConnector(BaseConnector):
    meta = ConnectorMeta(
        ...,
        capabilities=[Capability.SYNC, Capability.WRITE],
        write_operations=[
            WriteOperation(name="create_issue", entity_type="issue", description="Create an issue",
                parameters=[
                    {"name": "title", "type": "string", "required": True, "description": "Issue title"},
                    {"name": "body", "type": "string", "required": False, "description": "Description"},
                ]),
            WriteOperation(name="update_issue", entity_type="issue", description="Update an issue",
                parameters=[
                    {"name": "id", "type": "string", "required": True, "description": "Issue ID (e.g. source_id from DB)"},
                    {"name": "title", "type": "string", "required": False, "description": "New title"},
                    {"name": "state", "type": "string", "required": False, "description": "New state"},
                ]),
        ],
    )

    async def write(self, operation: str, data: dict) -> dict:
        if operation == "create_issue":
            return await self._create_issue(data)
        if operation == "update_issue":
            issue_id = data.pop("id")  # use same key as in parameters
            return await self._update_issue(issue_id, data)
        raise ValueError(f"Unknown operation: {operation}")
```

**Parameter names must match.** The agent sends whatever keys you declare in `parameters`. Your `write()` implementation must read those same keys from `data` (e.g. `id` for the record identifier on updates). If you later add a legacy alias (e.g. `issue_id`), accept both: `data.pop("issue_id", None) or data.pop("id")`. See `hubspot.py` (update_deal, update_contact, update_company) for the pattern.

### Recipe 6: Connector with Actions

Declare `actions` in meta with **parameters** (so the agent and UI show correct inputs) and implement `execute_action()`.

```python
from connectors.registry import Capability, ConnectorAction

class SlackConnector(BaseConnector):
    meta = ConnectorMeta(
        ...,
        capabilities=[Capability.SYNC, Capability.ACTION],
        actions=[
            ConnectorAction(name="send_message", description="Send a Slack message",
                parameters=[
                    {"name": "channel", "type": "string", "required": True, "description": "Channel (e.g. #sales)"},
                    {"name": "text", "type": "string", "required": True, "description": "Message text"},
                ]),
        ],
    )

    async def execute_action(self, action: str, params: dict) -> dict:
        if action == "send_message":
            # Call Slack API with params["channel"], params["text"] ...
            return {"ok": True, "ts": "1234567890.123456"}
        raise ValueError(f"Unknown action: {action}")
```

### Recipe 7: Connector with Event Listener

Declare `event_types` in meta and implement `handle_event()`.

```python
from connectors.registry import Capability, EventType

class MyConnector(BaseConnector):
    meta = ConnectorMeta(
        ...,
        capabilities=[Capability.LISTEN],
        event_types=[
            EventType(name="issue_updated", description="Fired when an issue is updated"),
        ],
    )

    async def handle_event(self, event_type: str, payload: dict) -> None:
        if event_type == "issue_updated":
            # Process the event, store as activity, trigger workflow, etc.
            ...
```

---

## Data Type Decision Tree

When your connector syncs data, choose the right storage tier:

| Tier | When to use | What to do |
| ---- | ----------- | ---------- |
| 1    | Data fits deals, accounts, contacts, or activities | Return existing Pydantic models — no schema changes |
| 2    | Data fits `tracker_*` or `shared_files` tables | Use existing tables with your `source_system` value |
| 3    | Data needs a new table | Include SQLAlchemy model + Alembic migration + RLS policy in your PR |

---

## Authentication

### OAuth2 (via Nango)

Set `auth_type=AuthType.OAUTH2` and `nango_integration_id="your-provider"`.
Nango handles the full OAuth flow. Your connector calls `self.get_oauth_token()`
to get the access token.

### API Key / Bearer Token

Set `auth_type=AuthType.API_KEY`. Credentials are stored in Nango and
retrieved via `self.get_oauth_token()`.

### Custom Auth

Set `auth_type=AuthType.CUSTOM` and declare `auth_fields` in meta. The
frontend renders a form based on these fields. Credentials are available
via `self.credentials` at runtime.

### Built-in / one-click connectors

Some connectors (e.g. `web_search`, `code_sandbox`, `twilio`) use platform
credentials and appear as “Connect” in the Connectors tab with no OAuth or
custom form. They are listed in the backend as built-in and use
`POST /integrations/connect-builtin`. The agent only sees them in the
manifest when they are enabled for the org.

---

## Testing

Run the Revtops dev stack with your connector installed:

```bash
cd backend
pip install -e .
uvicorn api.main:app --reload
```

Your connector will be auto-discovered. Check that it appears at
`GET /api/connectors`.

For unit tests, test your `sync_*` methods in isolation by asserting
on the returned Pydantic objects:

```python
async def test_sync_deals():
    connector = MyConnector(organization_id="test-org")
    deals = await connector.sync_deals()
    assert len(deals) > 0
    assert all(isinstance(d, DealRecord) for d in deals)
    assert all(d.source_system == "my_crm" for d in deals)
```

---

## PR Checklist

- [ ] `meta` attribute with correct capabilities, entity_types, auth config
- [ ] All abstract methods implemented (even if returning 0/empty)
- [ ] WRITE: `write_operations` include `parameters`; `write()` reads the same keys from `data` as declared (e.g. `id` for updates)
- [ ] ACTION: `actions` include `parameters` so the agent and UI show correct inputs
- [ ] New tables include `organization_id` and RLS policies (Tier 3 only)
- [ ] Rate limiting / retry logic for external API calls
- [ ] Error handling (don't let one bad record fail the entire sync)
- [ ] No secrets or credentials in code
- [ ] Tests for sync methods

---

## External Packages (Escape Hatch)

If you need a connector that lives outside the main repo (proprietary,
hyper-niche, rapid iteration), create a Python package with an entry point:

```toml
# pyproject.toml
[project.entry-points."revtops.connectors"]
my_crm = "my_package:MyCrmConnector"
```

Install it alongside Revtops (`pip install revtops-connector-mycrm`) and
the system will discover it automatically via `importlib.metadata.entry_points`.
