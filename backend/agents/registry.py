"""
Unified Tool Registry for Basebase Agent.

This module defines all tools available to the agent with:
- Categories (local_read, local_write, external_read, external_write)
- Default approval requirements
- Tool metadata for Claude

Mental Model ("Cursor for your business"):
- LOCAL_READ: Query synced data - always safe (like reading files)
- LOCAL_WRITE: Modify synced data - tracked, reversible (like editing files)
- EXTERNAL_READ: Web search, enrichment - may cost $ (like API calls)
- EXTERNAL_WRITE: CRM, email, Slack - permanent, external (like git push)
"""

import re
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

    status_running: str = ""
    """Human-friendly text when the tool starts (e.g. 'Querying your database'). Use {connector}, {provider}, etc. for placeholders."""
    status_complete: str = ""
    """Human-friendly text when the tool finishes (e.g. 'Queried your database')."""
    hidden_status: bool = False
    """If True, do not show status in Slack/Teams/UI (think, keep_notes, manage_memory)."""

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
    *,
    status_running: str = "",
    status_complete: str = "",
    hidden_status: bool = False,
) -> None:
    """Register a tool in the registry."""
    TOOL_DEFINITIONS[name] = ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        category=category,
        default_requires_approval=default_requires_approval,
        workflow_only=workflow_only,
        status_running=status_running,
        status_complete=status_complete,
        hidden_status=hidden_status,
    )


# -----------------------------------------------------------------------------
# LOCAL_READ Tools - Always safe, no approval
# -----------------------------------------------------------------------------

register_tool(
    name="run_sql_query",
    description="""Execute a read-only SQL SELECT query against the database.

Use this for any data analysis: filtering, joins, aggregations, date comparisons, etc.
The query is automatically scoped to the user's organization for multi-tenant tables.

Available tables (use these exact column names):
- meetings: id, title, scheduled_start, scheduled_end, summary, participants (JSONB), status, duration_minutes, organizer_email, account_id. Use scheduled_start/scheduled_end for dates (not start_time/end_time). Canonical meeting entities - deduplicated across all sources.
- deals: Sales opportunities (name, amount, stage, close_date, owner_id, account_id)
- accounts: Companies/customers (name, domain, industry, employee_count)
- contacts: People at accounts (name, email, title, phone, account_id)
- activities: id, type, subject, description, activity_date, embedding (vector). Raw activity records - query by TYPE not source. Use semantic_embed() for semantic search (see below). Do not query information_schema - only the tables listed here are allowed.
- pipelines: Sales pipelines (name, display_order, is_default)
- pipeline_stages: Stages in pipelines (pipeline_id, name, probability)
- goals: Revenue goals and quotas synced from CRM (name, target_amount, start_date, end_date, goal_type, owner_id, pipeline_id, source_system, source_id, custom_fields JSONB). Compare target_amount against deal totals to measure progress.
- integrations: Connected data sources (provider, is_active, last_sync_at)
- users: Team members (email, name, role, phone_number in E.164 format e.g. +14155551234)
- user_mappings_for_identity: Slack identity links (external_userid, external_email, match_source)
- organizations: User's company info (name, logo_url)
- conversations: Conversation threads visible to the current user (their own conversations plus org-shared conversations). Columns include id, user_id, title, summary, scope, source, participating_user_ids, created_at, updated_at.
- chat_messages: Messages from conversations visible to the current user. Columns include id, conversation_id, role, content_blocks (JSONB), user_id, created_at.
- workflows: Workflow definitions (name, trigger_type, prompt, is_enabled, auto_approve_tools). Useful for listing and inspecting workflows.
- workflow_runs: Workflow execution history (workflow_id, status, started_at, completed_at, output, workflow_notes). Useful for querying past run outcomes and notes.
- github_repositories: Tracked GitHub repos (full_name, owner, name, is_tracked, last_sync_at). Join to commits/PRs via repository_id.
- github_commits: Commits on tracked repos (repository_id, sha, message, author_name, author_email, author_login, author_date, additions, deletions, user_id).
- github_pull_requests: PRs on tracked repos (repository_id, number, title, state, author_login, created_date, merged_date, additions, deletions, user_id).
- tracker_teams: Issue tracker teams/workspaces (source_system, source_id, name, key, description). Filter by source_system ('linear', 'jira', 'asana'). Join to issues via team_id.
- tracker_projects: Issue tracker projects (source_system, source_id, name, description, state, progress, target_date, start_date, lead_name, team_ids JSONB). Filter by source_system.
- tracker_issues: Issue tracker issues/tasks (source_system, source_id, team_id, identifier e.g. "ENG-123", title, description, state_name, state_type, priority 0-4, priority_label, issue_type, assignee_name, assignee_email, creator_name, project_id, labels JSONB, estimate, url, due_date, created_date, updated_date, completed_date, cancelled_date, user_id). Filter by source_system.
- shared_files: Synced file metadata from cloud sources like Google Drive (external_id, source, name, mime_type, folder_path, web_view_link, file_size, source_modified_at). Filter by source (e.g. 'google_drive'). Use query_on_connector(connector='google_drive', query='search:...') for name-based searches.
- temp_data: Agent-generated results and computed metrics. Flexible JSONB storage linked to entities. Columns: entity_type, entity_id, namespace, key, value (JSONB), metadata (JSONB), created_by_user_id, created_at, expires_at. Example: SELECT td.value->>'score' as confidence, d.name FROM temp_data td JOIN deals d ON d.id = td.entity_id WHERE td.namespace = 'deal_confidence'
- daily_digests: Per-member daily activity digest. Columns: id, user_id, digest_date (DATE), summary (JSONB with keys: narrative, highlights, categories), raw_data (JSONB, nullable), generated_at. One row per user per date. Join to users via user_id. All org members' digests are visible.
- daily_team_summaries: Org-wide daily team summary. Columns: id, digest_date (DATE), summary_text (TEXT), generated_at. One row per date. Read-only.

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
    status_running="Querying your database",
    status_complete="Queried your database",
)


register_tool(
    name="search_documents",
    description="""Search documents (artifacts) created by the agent across all conversations.

Use this when the user asks to find a report, analysis, document, or file that was previously
created in any chat. Searches by title and description. Returns metadata (title, type, creation
date, conversation link) without the full content — use run_sql_query on the artifacts table
to retrieve content if needed.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term to match against document titles and descriptions",
            },
            "content_type": {
                "type": "string",
                "description": "Optional filter: 'markdown', 'text', 'pdf', 'chart'",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 20, max 50)",
            },
        },
        "required": ["query"],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=False,
    status_running="Searching documents",
    status_complete="Found documents",
)


register_tool(
    name="list_connected_connectors",
    description="""Refresh and return the capabilities manifest for all connected connectors.

Use this to get an up-to-date list of connected connectors and their capabilities
(query, write, action). The manifest shows available operations and their parameters
for each connector. Useful when the user asks about available connectors or when you
need to verify a connector is connected before using it.""",
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=False,
    status_running="Checking connected tools",
    status_complete="Checked connected tools",
)


register_tool(
    name="get_connector_docs",
    description="""Get detailed usage documentation for a connected connector.

Call this before using query_on_connector, write_on_connector, or run_on_connector for a connector
you haven't used yet. Returns rich usage guides (query formats, action parameters,
examples) written by the connector author. Use the connector slug (e.g. 'google_drive',
'hubspot', 'slack') from the Connected Connectors manifest.""",
    input_schema={
        "type": "object",
        "properties": {
            "connector": {
                "type": "string",
                "description": "Connector slug (e.g. 'google_drive', 'hubspot', 'slack')",
            },
        },
        "required": ["connector"],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=False,
    status_running="Reading {connector} docs",
    status_complete="Read {connector} docs",
)


register_tool(
    name="query_on_connector",
    description="""Query a connected connector for on-demand data retrieval.

Use this for any QUERY-capable connector: web search (web_search), Apollo enrichment,
Google Drive file search/read, Granola meeting search (granola), or any other connector with query capability.

The query string format depends on the connector. Call `get_connector_docs(connector)` for
detailed query formats, parameters, and examples before using a connector.""",
    input_schema={
        "type": "object",
        "properties": {
            "connector": {
                "type": "string",
                "description": "Connector slug (e.g. 'web_search', 'apollo', 'google_drive')",
            },
            "query": {
                "type": "string",
                "description": "Query string — format depends on the connector (see Connected Connectors manifest)",
            },
        },
        "required": ["connector", "query"],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
    status_running="Looking up data in {connector}",
    status_complete="Queried {connector}",
)


register_tool(
    name="write_on_connector",
    description="""Create or update records in a connected connector.

Use this for any WRITE-capable connector: HubSpot (deals, contacts, companies),
GitHub/Linear/Asana (issues), or any future connector with write capability.

Check the Connected Connectors manifest for available operations and their required parameters.""",
    input_schema={
        "type": "object",
        "properties": {
            "connector": {
                "type": "string",
                "description": "Connector slug (e.g. 'hubspot', 'linear', 'github', 'asana')",
            },
            "operation": {
                "type": "string",
                "description": "Write operation name (e.g. 'create_deal', 'update_issue')",
            },
            "data": {
                "type": "object",
                "description": "Record data — fields depend on connector and operation (see Connected Connectors manifest)",
            },
        },
        "required": ["connector", "operation", "data"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=False,
    status_running="Writing to {connector}",
    status_complete="Wrote to {connector}",
)


register_tool(
    name="run_on_connector",
    description="""Execute a side-effect action on a connected connector.

Use this for any ACTION-capable connector: sending Slack messages, sending emails
(Gmail/Outlook), sending SMS (Twilio), fetching URLs (web_search), creating Google Drive
files, executing sandbox commands (code_sandbox), or any future connector with action capability.

Check the Connected Connectors manifest for available actions and their required parameters.""",
    input_schema={
        "type": "object",
        "properties": {
            "connector": {
                "type": "string",
                "description": "Connector slug (e.g. 'slack', 'gmail', 'twilio', 'web_search', 'code_sandbox')",
            },
            "action": {
                "type": "string",
                "description": "Action name (e.g. 'send_message', 'send_email', 'send_sms', 'fetch_url', 'execute_command')",
            },
            "params": {
                "type": "object",
                "description": "Action parameters — fields depend on connector and action (see Connected Connectors manifest)",
            },
        },
        "required": ["connector", "action", "params"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=False,
    status_running="Running action on {connector}",
    status_complete="Ran action on {connector}",
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
- daily_digests: (summary) → immediate, user can only update their own digest. summary is JSONB with keys: narrative (string), highlights (array), categories (array). user_id is auto-set to the current user on INSERT.

**WORKFLOW FORMAT (Important!):**
Workflows are prompts sent to the agent on a schedule. Use these columns:
- name: Human-readable name
- prompt: Natural language instructions for what the agent should do
- trigger_type: 'schedule', 'event', or 'manual'
- trigger_config: JSON with cron expression, e.g. '{"cron": "0 9 * * *"}'
- auto_approve_tools: JSON array of tools that run without approval, e.g. '["run_sql_query", "run_on_connector"]'

DO NOT use the 'steps' column - it's deprecated. Use 'prompt' instead.

Workflow example:
INSERT INTO workflows (name, prompt, trigger_type, trigger_config, auto_approve_tools)
VALUES (
  'Daily Deal Summary',
  'Query deals to get a summary of open opportunities. Include total count, value by stage, and top 5 deals by amount. Format a nice summary with emojis and post to #sales on Slack.',
  'schedule',
  '{"cron": "0 9 * * *"}',
  '["run_sql_query", "run_on_connector"]'
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
    status_running="Updating your database",
    status_complete="Updated your database",
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
    status_running="Syncing {provider}",
    status_complete="Synced {provider}",
)


register_tool(
    name="initiate_connector",
    description="""Initiate the OAuth connection flow for a new connector.

Use this when the user asks to connect a new integration like Jira, Salesforce, HubSpot, Slack, etc.
This opens an OAuth popup in the user's browser to authorize the connection.

All connectors are user-scoped: each user connects their own account. For some connectors (e.g. HubSpot, Linear), the user can optionally share query or write access with teammates so others can use the data or act through that connection.

Available connectors include:
- CRM: hubspot, salesforce
- Communication: slack
- Issue tracking: jira, linear, asana
- Code: github
- Email/Calendar: gmail, google_calendar, microsoft_mail, microsoft_calendar
- Storage: google_drive
- Meetings: zoom, fireflies
- Data enrichment: apollo
- Built-in: web_search, code_sandbox, twilio""",
    input_schema={
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "description": "The connector to connect (e.g., 'jira', 'salesforce', 'hubspot', 'slack')",
            },
        },
        "required": ["provider"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
    status_running="Setting up connection",
    status_complete="Set up connection",
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
    status_running="Running workflow",
    status_complete="Completed workflow",
)


register_tool(
    name="foreach",
    description="""Run a tool or workflow for each item in a list.

Use this for any batch operation: enriching contacts, researching companies, sending emails, processing data, etc.

Provide EITHER tool (to call a tool per item) OR workflow_id (to run a workflow per item).

**Tool mode** — each item renders params_template ({{field}} placeholders filled from item), then the tool is called. Results stored in bulk_operation_results (queryable via run_sql_query). Uses distributed Celery workers.
  Example: foreach(tool="query_on_connector", items_query="SELECT id, name FROM contacts", params_template={"connector": "web_search", "query": "Current role of {{name}}?"})

**Workflow mode** — each item dict is passed as input_data to the workflow. Uses async in-process execution with context propagation.
  Example: foreach(workflow_id="uuid-...", items=[{"email": "a@b.com"}, {"email": "c@d.com"}])

Set max_concurrent=1 for sequential execution, higher for parallel. Blocks until all items complete, streaming live progress to the UI.""",
    input_schema={
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Name of the tool to run per item (e.g., 'query_on_connector', 'run_on_connector'). Mutually exclusive with workflow_id.",
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
    status_running="Processing items",
    status_complete="Processed items",
)

# -----------------------------------------------------------------------------
# User Memory Tools - Save/delete persistent per-user preferences
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Workflow Notes Tool - Save workflow-scoped notes shared across runs
# -----------------------------------------------------------------------------

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
    hidden_status=True,
)

register_tool(
    name="think",
    description="""Use this tool to plan your approach before taking action on complex, multi-step tasks.

Call this when the request involves multiple tools, data dependencies between steps, or
non-obvious ordering. Write out your reasoning: what information you need, which tools
to call in what order, and what could go wrong.

You do NOT need to call this for simple, single-tool requests. Only use it when planning
genuinely helps — e.g. multi-query analysis, connector workflows with dependencies,
bulk operations, or ambiguous requests that need decomposition.""",
    input_schema={
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "Your step-by-step plan or reasoning about how to approach the task.",
            },
        },
        "required": ["thought"],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=False,
    hidden_status=True,
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
- "organization_member": Facts about the user's specific role.

Organization-scoped memories are not allowed.

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
                "enum": ["user", "organization_member"],
                "description": "Scope level. Defaults to 'user'. Organization-scoped memories are not allowed.",
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
    hidden_status=True,
)



# =============================================================================
# Helper Functions
# =============================================================================

def _title_slug(slug: str) -> str:
    """Turn a connector/provider slug into a display label (e.g. google_drive -> Google Drive)."""
    if not slug or not isinstance(slug, str):
        return ""
    return slug.replace("_", " ").strip().title()


def format_tool_status(
    tool_name: str,
    tool_input: dict[str, Any],
    phase: str,
) -> str | None:
    """Return human-friendly status text for a tool call, or None if hidden or unknown.

    phase must be 'running' or 'complete'. Template placeholders like {connector}
    are filled from tool_input with title-cased display labels.
    """
    tool: ToolDefinition | None = TOOL_DEFINITIONS.get(tool_name)
    if tool is None or tool.hidden_status:
        return None
    template: str = tool.status_running if phase == "running" else tool.status_complete
    if not template:
        return None
    raw: dict[str, Any] = tool_input or {}
    placeholders: list[str] = re.findall(r"\{(\w+)\}", template)
    format_map: dict[str, str] = {}
    for key in placeholders:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            format_map[key] = _title_slug(val)
        else:
            format_map[key] = key.replace("_", " ").title()
    try:
        return template.format(**format_map)
    except KeyError:
        return template


def get_tool(name: str) -> ToolDefinition | None:
    """Get a tool definition by name."""
    return TOOL_DEFINITIONS.get(name)


def get_all_tools() -> list[ToolDefinition]:
    """Get all registered tool definitions."""
    return list(TOOL_DEFINITIONS.values())


def get_tools_for_claude(in_workflow: bool = False) -> list[dict[str, Any]]:
    """Get tool definitions formatted for Claude's API (legacy, use get_tool_defs for provider-agnostic)."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in TOOL_DEFINITIONS.values()
        if in_workflow or not tool.workflow_only
    ]


def get_tool_defs(in_workflow: bool = False) -> list["ToolDef"]:
    """Get provider-agnostic tool definitions. Adapters translate to vendor-specific format."""
    from services.llm_adapter import ToolDef

    return [
        ToolDef(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
        )
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
