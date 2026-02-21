"""
Unified Tool Registry for Revtops Agent.

This module defines all tools available to the agent with:
- Categories (local_read, local_write, external_read, external_write)
- Default approval requirements
- Tool metadata for Claude

Mental Model ("Cursor for GTM"):
- LOCAL_READ: Query synced data - always safe (like reading files)
- LOCAL_WRITE: Modify synced data - tracked, reversible (like editing files)
- EXTERNAL_READ: Web search, enrichment - may cost $ (like API calls)
- EXTERNAL_WRITE: CRM, email, Slack - permanent, external (like git push)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


class ToolCategory(Enum):
    """Categories of tools based on their risk/reversibility profile."""
    
    LOCAL_READ = "local_read"
    """Query synced data - always safe, no approval needed."""
    
    LOCAL_WRITE = "local_write"
    """Modify synced data - tracked in change sessions, reversible."""
    
    EXTERNAL_READ = "external_read"
    """Web search, enrichment APIs - may cost money but no side effects."""
    
    EXTERNAL_WRITE = "external_write"
    """CRM writes, emails, Slack - permanent external actions."""


@dataclass
class ToolDefinition:
    """Definition of a tool available to the agent."""
    
    name: str
    """Unique identifier for the tool."""
    
    description: str
    """Description shown to Claude explaining when/how to use the tool."""
    
    input_schema: dict[str, Any]
    """JSON Schema for the tool's input parameters."""
    
    category: ToolCategory
    """Category determining default approval behavior."""
    
    default_requires_approval: bool
    """Whether this tool requires user approval by default."""

    workflow_only: bool = False
    """Whether this tool should only be exposed during workflow executions."""
    
    # Note: execute_fn is set separately to avoid circular imports
    # The actual execution functions are in tools.py


# =============================================================================
# Tool Definitions
# =============================================================================

TOOL_DEFINITIONS: dict[str, ToolDefinition] = {}


def register_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    category: ToolCategory,
    default_requires_approval: bool = False,
    workflow_only: bool = False,
) -> None:
    """Register a tool in the registry."""
    TOOL_DEFINITIONS[name] = ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        category=category,
        default_requires_approval=default_requires_approval,
        workflow_only=workflow_only,
    )


# -----------------------------------------------------------------------------
# LOCAL_READ Tools - Always safe, no approval
# -----------------------------------------------------------------------------

register_tool(
    name="run_sql_query",
    description="""Execute a read-only SQL SELECT query against the database.

Use this for any data analysis: filtering, joins, aggregations, date comparisons, etc.
The query is automatically scoped to the user's organization for multi-tenant tables.

Available tables:
- meetings: Canonical meeting entities - deduplicated across all sources
- deals: Sales opportunities (name, amount, stage, close_date, owner_id, account_id)
- accounts: Companies/customers (name, domain, industry, employee_count)
- contacts: People at accounts (name, email, title, phone, account_id)
- activities: Raw activity records - query by TYPE not source. Has a vector embedding column for semantic search (see below)
- pipelines: Sales pipelines (name, display_order, is_default)
- pipeline_stages: Stages in pipelines (pipeline_id, name, probability)
- goals: Revenue goals and quotas synced from CRM (name, target_amount, start_date, end_date, goal_type, owner_id, pipeline_id, source_system, source_id, custom_fields JSONB). Compare target_amount against deal totals to measure progress.
- integrations: Connected data sources (provider, is_active, last_sync_at)
- users: Team members (email, name, role, phone_number in E.164 format e.g. +14155551234)
- user_mappings_for_identity: Slack identity links (external_userid, external_email, match_source)
- organizations: User's company info (name, logo_url)
- workflows: Workflow definitions (name, trigger_type, prompt, is_enabled, auto_approve_tools). Useful for listing and inspecting automations.
- workflow_runs: Workflow execution history (workflow_id, status, started_at, completed_at, output, workflow_notes). Useful for querying past run outcomes and notes.
- github_repositories: Tracked GitHub repos (full_name, owner, name, is_tracked, last_sync_at). Join to commits/PRs via repository_id.
- github_commits: Commits on tracked repos (repository_id, sha, message, author_name, author_email, author_login, author_date, additions, deletions, user_id).
- github_pull_requests: PRs on tracked repos (repository_id, number, title, state, author_login, created_date, merged_date, additions, deletions, user_id).
- tracker_teams: Issue tracker teams/workspaces (source_system, source_id, name, key, description). Filter by source_system ('linear', 'jira', 'asana'). Join to issues via team_id.
- tracker_projects: Issue tracker projects (source_system, source_id, name, description, state, progress, target_date, start_date, lead_name, team_ids JSONB). Filter by source_system.
- tracker_issues: Issue tracker issues/tasks (source_system, source_id, team_id, identifier e.g. "ENG-123", title, description, state_name, state_type, priority 0-4, priority_label, issue_type, assignee_name, assignee_email, creator_name, project_id, labels JSONB, estimate, url, due_date, created_date, updated_date, completed_date, cancelled_date, user_id). Filter by source_system.
- shared_files: Synced file metadata from cloud sources like Google Drive (external_id, source, name, mime_type, folder_path, web_view_link, file_size, source_modified_at). Filter by source (e.g. 'google_drive'). Use query_system(system='google_drive', query='search:...') for name-based searches.
- temp_data: Agent-generated results and computed metrics. Flexible JSONB storage linked to entities. Columns: entity_type, entity_id, namespace, key, value (JSONB), metadata (JSONB), created_by_user_id, created_at, expires_at. Example: SELECT td.value->>'score' as confidence, d.name FROM temp_data td JOIN deals d ON d.id = td.entity_id WHERE td.namespace = 'deal_confidence'

SEMANTIC SEARCH on activities: Use semantic_embed('natural language query') as an inline function to generate an embedding vector. Combine with the <=> cosine distance operator to rank by similarity.
Example: SELECT id, type, subject, description, activity_date, 1 - (embedding <=> semantic_embed('pricing negotiations')) AS similarity FROM activities WHERE embedding IS NOT NULL ORDER BY embedding <=> semantic_embed('pricing negotiations') LIMIT 10
You can add extra WHERE clauses (e.g. type = 'email') alongside the vector search.

IMPORTANT: Do NOT add organization_id to WHERE clauses. Data is automatically scoped to the user's organization via row-level security. Adding organization_id filters will cause queries to return wrong results.

IMPORTANT: Only SELECT queries are allowed. No INSERT, UPDATE, DELETE, DROP, etc.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The SQL SELECT query to execute",
            },
        },
        "required": ["query"],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=False,
)


register_tool(
    name="list_connected_systems",
    description="""Refresh and return the capabilities manifest for all connected systems.

Use this to get an up-to-date list of connected integrations and their capabilities
(query, write, action). The manifest shows available operations and their parameters
for each system. Useful when the user asks about available integrations or when you
need to verify a system is connected before using it.""",
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=False,
)


register_tool(
    name="query_system",
    description="""Query a connected system for on-demand data retrieval.

Use this for any QUERY-capable connector: web search (web_search), Apollo enrichment,
Google Drive file search/read, or any future connector with query capability.

The query string format depends on the system — check the Connected Systems manifest
in the system prompt for each system's query_description.""",
    input_schema={
        "type": "object",
        "properties": {
            "system": {
                "type": "string",
                "description": "Connector slug (e.g. 'web_search', 'apollo', 'google_drive')",
            },
            "query": {
                "type": "string",
                "description": "Query string — format depends on the system (see Connected Systems manifest)",
            },
        },
        "required": ["system", "query"],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
)


register_tool(
    name="write_to_system",
    description="""Create or update records in a connected system.

Use this for any WRITE-capable connector: HubSpot (deals, contacts, companies),
GitHub/Linear/Asana (issues), or any future connector with write capability.

Check the Connected Systems manifest for available operations and their required parameters.""",
    input_schema={
        "type": "object",
        "properties": {
            "system": {
                "type": "string",
                "description": "Connector slug (e.g. 'hubspot', 'linear', 'github', 'asana')",
            },
            "operation": {
                "type": "string",
                "description": "Write operation name (e.g. 'create_deal', 'update_issue')",
            },
            "data": {
                "type": "object",
                "description": "Record data — fields depend on system and operation (see Connected Systems manifest)",
            },
        },
        "required": ["system", "operation", "data"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=False,
)


register_tool(
    name="run_action",
    description="""Execute a side-effect action on a connected system.

Use this for any ACTION-capable connector: sending Slack messages, sending emails
(Gmail/Outlook), sending SMS (Twilio), fetching URLs (web_search), creating Google Drive
files, executing sandbox commands (code_sandbox), or any future connector with action capability.

Check the Connected Systems manifest for available actions and their required parameters.""",
    input_schema={
        "type": "object",
        "properties": {
            "system": {
                "type": "string",
                "description": "Connector slug (e.g. 'slack', 'gmail', 'twilio', 'web_search', 'code_sandbox')",
            },
            "action": {
                "type": "string",
                "description": "Action name (e.g. 'send_message', 'send_email', 'send_sms', 'fetch_url', 'execute_command')",
            },
            "params": {
                "type": "object",
                "description": "Action parameters — fields depend on system and action (see Connected Systems manifest)",
            },
        },
        "required": ["system", "action", "params"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=False,
)


# -----------------------------------------------------------------------------
# LOCAL_WRITE Tools - Tracked in change sessions, reversible
# -----------------------------------------------------------------------------

register_tool(
    name="run_sql_write",
    description="""Execute an INSERT, UPDATE, or DELETE query against the database.

Use this to create, modify, or delete records. The query is automatically scoped 
to the user's organization - you cannot affect other organizations' data.

**CRM Tables (contacts, deals, accounts):**
These go through a review workflow - changes are queued as "pending" and the user 
can commit to HubSpot or undo via the bottom panel.

**Other Tables (workflows, artifacts):**
These execute immediately since they're internal-only data.

Writable tables:
- contacts: (email, firstname, lastname, company, jobtitle, phone) → pending review
- deals: (dealname, amount, dealstage, closedate) → pending review  
- accounts: (name, domain, industry, numberofemployees) → pending review
- users: (phone_number) → immediate. Phone numbers MUST be E.164 format with country code, e.g. +14155551234 for US numbers. If the user gives a 10-digit number like 4159028648, prepend +1.
- workflows: See workflow format below → immediate
- artifacts: (type, title, description, data) → immediate
- temp_data: (entity_type, entity_id, namespace, key, value, metadata, expires_at) → immediate. Flexible JSONB store for computed results. Use namespace to group (e.g. 'deal_confidence'). value is JSONB, any shape.

**WORKFLOW FORMAT (Important!):**
Workflows are prompts sent to the agent on a schedule. Use these columns:
- name: Human-readable name
- prompt: Natural language instructions for what the agent should do
- trigger_type: 'schedule', 'event', or 'manual'
- trigger_config: JSON with cron expression, e.g. '{"cron": "0 9 * * *"}'
- auto_approve_tools: JSON array of tools that run without approval, e.g. '["run_sql_query", "run_action"]'

DO NOT use the 'steps' column - it's deprecated. Use 'prompt' instead.

Workflow example:
INSERT INTO workflows (name, prompt, trigger_type, trigger_config, auto_approve_tools)
VALUES (
  'Daily Deal Summary',
  'Query deals to get a summary of open opportunities. Include total count, value by stage, and top 5 deals by amount. Format a nice summary with emojis and post to #sales on Slack.',
  'schedule',
  '{"cron": "0 9 * * *"}',
  '["run_sql_query", "run_action"]'
)

Auto-managed columns (don't include):
- id, organization_id, created_at, updated_at, created_by_user_id

Rules:
- UPDATE and DELETE require a WHERE clause with id = '...'""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The SQL INSERT, UPDATE, or DELETE query to execute",
            },
        },
        "required": ["query"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)


register_tool(
    name="create_artifact",
    description="""Create a downloadable artifact (file) for the user.

Use this to create files the user can view, interact with, and download:
- Text files (.txt) - plain text content
- Markdown documents (.md) - formatted documentation, reports  
- PDF documents (.pdf) - provide content as markdown, will be converted
- Charts (.html) - interactive Plotly charts

The artifact appears as a clickable tile in the chat. When clicked, it opens 
in a side panel where the user can view and download it.

For charts, provide a valid Plotly JSON specification as the content.
Example chart content:
{
  "data": [{"type": "bar", "x": ["Q1", "Q2", "Q3"], "y": [10, 20, 30]}],
  "layout": {"title": "Quarterly Sales"}
}

For PDFs, write the content in markdown format - it will be converted to PDF.""",
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Display title for the artifact",
            },
            "filename": {
                "type": "string",
                "description": "Filename with extension (e.g., 'report.pdf', 'summary.md', 'chart.html')",
            },
            "content_type": {
                "type": "string",
                "enum": ["text", "markdown", "pdf", "chart"],
                "description": "Type of artifact to create",
            },
            "content": {
                "type": "string",
                "description": "File content: text/markdown for text types, Plotly JSON for charts",
            },
        },
        "required": ["title", "filename", "content_type", "content"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)


# -----------------------------------------------------------------------------
# EXTERNAL_WRITE Tools - Permanent external actions
# -----------------------------------------------------------------------------

register_tool(
    name="trigger_sync",
    description="""Trigger a data sync for a specific integration.

Use this when the user wants to refresh data from a connected source.
The sync runs in the background and may take a few minutes to complete.""",
    input_schema={
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "description": "The provider to sync (e.g., 'hubspot', 'salesforce', 'gmail')",
            },
        },
        "required": ["provider"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=False,  # Syncing is safe, just refreshes data
)


# -----------------------------------------------------------------------------
# Workflow Management Tools
# -----------------------------------------------------------------------------

register_tool(
    name="run_workflow",
    description="""Execute a workflow — either to test it manually or to compose workflows.

Use cases:
1. **Manual trigger**: Set wait_for_completion=false to fire-and-forget (e.g. "test this workflow now").
2. **Workflow composition**: A parent workflow delegates to specialist child workflows and gets the result back.

For example, an "Enrich All Contacts" workflow could call "Enrich Single Contact" for each contact.

The child workflow executes with its own conversation and returns its output.
Input data is passed to the child workflow and available in its prompt context.

IMPORTANT: Avoid circular calls (A calls B calls A) - this will be detected and rejected.""",
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "UUID of the workflow to execute",
            },
            "input_data": {
                "type": "object",
                "description": "Data to pass to the child workflow (available as trigger_data)",
            },
            "wait_for_completion": {
                "type": "boolean",
                "description": "If true, wait for workflow to complete and return result. If false, fire and forget.",
                "default": True,
            },
        },
        "required": ["workflow_id"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)


register_tool(
    name="foreach",
    description="""Run a tool or workflow for each item in a list.

Use this for any batch operation: enriching contacts, researching companies, sending emails, processing data, etc.

Provide EITHER tool (to call a tool per item) OR workflow_id (to run a workflow per item).

**Tool mode** — each item renders params_template ({{field}} placeholders filled from item), then the tool is called. Results stored in bulk_operation_results (queryable via run_sql_query). Uses distributed Celery workers.
  Example: foreach(tool="query_system", items_query="SELECT id, name FROM contacts", params_template={"system": "web_search", "query": "Current role of {{name}}?"})

**Workflow mode** — each item dict is passed as input_data to the workflow. Uses async in-process execution with context propagation.
  Example: foreach(workflow_id="uuid-...", items=[{"email": "a@b.com"}, {"email": "c@d.com"}])

Set max_concurrent=1 for sequential execution, higher for parallel. Blocks until all items complete, streaming live progress to the UI.""",
    input_schema={
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Name of the tool to run per item (e.g., 'query_system', 'run_action'). Mutually exclusive with workflow_id.",
            },
            "workflow_id": {
                "type": "string",
                "description": "UUID of the workflow to run per item. Each item dict becomes input_data. Mutually exclusive with tool.",
            },
            "items_query": {
                "type": "string",
                "description": "SQL SELECT that returns the items to process. Column names map to {{placeholder}} names in params_template.",
            },
            "items": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Inline list of item dicts. Use items_query for large lists (>100 items).",
            },
            "params_template": {
                "type": "object",
                "description": "Template for tool params. Use {{column_name}} placeholders filled from each item. Required when using tool, ignored for workflow_id.",
            },
            "max_concurrent": {
                "type": "integer",
                "description": "Parallel executions (1 = sequential). Default 5. Max 10 for workflows, 50 for tools.",
                "default": 5,
            },
            "rate_limit_per_minute": {
                "type": "integer",
                "description": "Max API calls per minute (tool mode only). Default 200.",
                "default": 200,
            },
            "max_items": {
                "type": "integer",
                "description": "Safety cap on total items. Default 500 for workflows, 10000 for tools.",
            },
            "continue_on_error": {
                "type": "boolean",
                "description": "Keep processing if an item fails (default true).",
                "default": True,
            },
            "operation_name": {
                "type": "string",
                "description": "Human-readable name shown in progress updates.",
            },
        },
        "required": [],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)

# -----------------------------------------------------------------------------
# User Memory Tools - Save/delete persistent per-user preferences
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Workflow Notes Tool - Save workflow-scoped notes shared across runs
# -----------------------------------------------------------------------------

register_tool(
    name="create_app",
    description="""Create an interactive mini-app that queries data and renders it with React.

Use this when the user asks for a dashboard, interactive chart, or data view with controls
(dropdowns, date pickers, filters, etc.) that should refresh when the user changes them.

The app has two parts:
1. **queries** (server-side): Named parameterized SQL queries. These stay on the server and are never exposed to the browser.
2. **frontend_code** (client-side): React JSX code that renders the UI and calls the queries via the SDK.

The React code runs in a sandboxed iframe with these pre-bundled packages:
- react, react-dom (hooks: useState, useEffect, useCallback, useMemo, useRef)
- react-plotly.js (import Plot from "react-plotly.js")
- @revtops/app-sdk (useAppQuery, useDateRange, Spinner, ErrorBanner)

**SDK API:**
- useAppQuery(queryName, params) → { data, columns, loading, error, refetch }
  - queryName: must match a key in the queries object
  - params: object with values for the SQL :placeholders
  - data: array of row objects, e.g. [{region: "West", revenue: 50000}, ...]
- useDateRange(period) → { start, end } (ISO date strings)
  - period: "last_7d", "last_30d", "last_90d", "last_quarter", "this_quarter", "ytd", "last_year", "this_year"
- Spinner — loading spinner component
- ErrorBanner({ message }) — error display component

**Query params:**
Each query declares its params with name, type, and required flag. The SDK sends
param values to the server, where they are bound via parameterized queries (safe from injection).

**Rules:**
- All SQL must be SELECT-only. No INSERT/UPDATE/DELETE.
- Do NOT add organization_id to WHERE clauses (RLS handles it).
- frontend_code must export a default React component.
- Use Tailwind-style inline CSS or the provided dark-theme base styles.
- Keep the code concise — one file, one default export.

**Example:**
{
  "title": "Revenue by Region",
  "description": "Bar chart showing revenue by region with time period filter",
  "queries": {
    "revenue_data": {
      "sql": "SELECT custom_fields->>'region' as region, SUM(amount) as revenue FROM deals WHERE close_date >= :start_date AND close_date <= :end_date AND stage = 'Closed Won' GROUP BY 1 ORDER BY revenue DESC",
      "params": {
        "start_date": { "type": "date", "required": true },
        "end_date": { "type": "date", "required": true }
      }
    }
  },
  "frontend_code": "import { useState } from 'react';\\nimport { useAppQuery, useDateRange, Spinner, ErrorBanner } from '@revtops/app-sdk';\\nimport Plot from 'react-plotly.js';\\n\\nexport default function App() {\\n  const [period, setPeriod] = useState('last_quarter');\\n  const { start, end } = useDateRange(period);\\n  const { data, loading, error } = useAppQuery('revenue_data', { start_date: start, end_date: end });\\n\\n  return (\\n    <div style={{padding:'1rem'}}>\\n      <select value={period} onChange={e => setPeriod(e.target.value)}>\\n        <option value='last_30d'>Last 30 Days</option>\\n        <option value='last_quarter'>Last Quarter</option>\\n        <option value='ytd'>Year to Date</option>\\n      </select>\\n      {loading && <Spinner />}\\n      {error && <ErrorBanner message={error} />}\\n      {data && <Plot data={[{type:'bar', x:data.map(r=>r.region), y:data.map(r=>r.revenue)}]} layout={{title:'Revenue by Region', autosize:true, paper_bgcolor:'transparent', plot_bgcolor:'transparent', font:{color:'#a1a1aa'}}} style={{width:'100%',height:'400px'}} config={{responsive:true}} />}\\n    </div>\\n  );\\n}"
}""",
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Display title for the app",
            },
            "description": {
                "type": "string",
                "description": "Brief description of what the app shows",
            },
            "queries": {
                "type": "object",
                "description": "Named SQL queries. Each key is a query name, value is {sql, params}. SQL must be SELECT-only.",
            },
            "frontend_code": {
                "type": "string",
                "description": "React JSX code (single file). Must export default component. Uses @revtops/app-sdk hooks.",
            },
        },
        "required": ["title", "queries", "frontend_code"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)


register_tool(
    name="keep_notes",
    description="""Store workflow-scoped notes that should be available to future runs of the same workflow.

Notes are persisted on `workflow_runs.workflow_notes`, which is the canonical field for workflow execution notes/state shared across runs of the same workflow.

Use this in workflow executions for interim findings, state, or progress breadcrumbs that future runs can reference.

This is workflow-scoped memory, not user-wide memory.""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The note content to store for this workflow.",
            },
        },
        "required": ["content"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
    workflow_only=True,
)

register_tool(
    name="manage_memory",
    description="""Save, update, or delete a persistent memory that is recalled at the start of every future conversation.

Actions:
- "save" (default): Create a new memory. Requires content.
- "update": Revise an existing memory. Requires memory_id and content.
- "delete": Remove a memory. Requires memory_id.

Memories are scoped via entity_type:
- "user": Personal facts/preferences (default).
- "organization": Company-wide facts shared across all members.
- "organization_member": Facts about the user's specific role.

Use when the user asks you to "remember" or "forget" something, or when a saved memory needs revision.
Each memory should be a single, self-contained statement. Do NOT save conversation-specific context.""",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "update", "delete"],
                "description": "What to do: save a new memory, update an existing one, or delete one.",
                "default": "save",
            },
            "content": {
                "type": "string",
                "description": "The memory content (required for save and update).",
            },
            "memory_id": {
                "type": "string",
                "description": "UUID of the memory to update or delete (required for update and delete).",
            },
            "entity_type": {
                "type": "string",
                "enum": ["user", "organization", "organization_member"],
                "description": "Scope level. Defaults to 'user'.",
                "default": "user",
            },
            "category": {
                "type": "string",
                "description": "Optional grouping category (e.g. 'preference', 'personal', 'professional').",
            },
        },
        "required": [],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)



# =============================================================================
# Helper Functions
# =============================================================================

def get_tool(name: str) -> ToolDefinition | None:
    """Get a tool definition by name."""
    return TOOL_DEFINITIONS.get(name)


def get_all_tools() -> list[ToolDefinition]:
    """Get all registered tool definitions."""
    return list(TOOL_DEFINITIONS.values())


def get_tools_for_claude(in_workflow: bool = False) -> list[dict[str, Any]]:
    """Get tool definitions formatted for Claude's API."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in TOOL_DEFINITIONS.values()
        if in_workflow or not tool.workflow_only
    ]


def get_tools_by_category(category: ToolCategory) -> list[ToolDefinition]:
    """Get all tools in a specific category."""
    return [t for t in TOOL_DEFINITIONS.values() if t.category == category]


def get_approval_required_tools() -> list[ToolDefinition]:
    """Get all tools that require approval by default."""
    return [t for t in TOOL_DEFINITIONS.values() if t.default_requires_approval]


def requires_approval(tool_name: str) -> bool:
    """Check if a tool requires approval by default."""
    tool = TOOL_DEFINITIONS.get(tool_name)
    return tool.default_requires_approval if tool else False
