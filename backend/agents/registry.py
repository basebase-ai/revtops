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
) -> None:
    """Register a tool in the registry."""
    TOOL_DEFINITIONS[name] = ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        category=category,
        default_requires_approval=default_requires_approval,
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
- activities: Raw activity records - query by TYPE not source
- pipelines: Sales pipelines (name, display_order, is_default)
- pipeline_stages: Stages in pipelines (pipeline_id, name, probability)
- integrations: Connected data sources (provider, is_active, last_sync_at)
- users: Team members (email, name, role)
- slack_user_mappings: Slack identity links (slack_user_id, slack_email, match_source)
- organizations: User's company info (name, logo_url)

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
    name="search_activities",
    description="""Semantic search across emails, meetings, messages, and other activities.

Use this when the user wants to find activities by meaning/concept rather than exact text.
This searches the content of emails, meeting transcripts, messages, etc.

Examples:
- "Find emails about pricing negotiations"
- "Search for meeting discussions about the Q4 roadmap"
- "Look for communications about contract renewal"

For exact text matching, use run_sql_query with ILIKE instead.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by type: 'email', 'meeting', 'meeting_transcript', 'slack_message', 'call'",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 10)",
                "default": 10,
            },
        },
        "required": ["query"],
    },
    category=ToolCategory.LOCAL_READ,
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
- workflows: See workflow format below → immediate
- artifacts: (type, title, description, data) → immediate

**WORKFLOW FORMAT (Important!):**
Workflows are prompts sent to the agent on a schedule. Use these columns:
- name: Human-readable name
- prompt: Natural language instructions for what the agent should do
- trigger_type: 'schedule', 'event', or 'manual'
- trigger_config: JSON with cron expression, e.g. '{"cron": "0 9 * * *"}'
- auto_approve_tools: JSON array of tools that run without approval, e.g. '["run_sql_query", "send_slack"]'

DO NOT use the 'steps' column - it's deprecated. Use 'prompt' instead.

Workflow example:
INSERT INTO workflows (name, prompt, trigger_type, trigger_config, auto_approve_tools)
VALUES (
  'Daily Deal Summary',
  'Query deals to get a summary of open opportunities. Include total count, value by stage, and top 5 deals by amount. Format a nice summary with emojis and post to #sales on Slack.',
  'schedule',
  '{"cron": "0 9 * * *"}',
  '["run_sql_query", "send_slack"]'
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
# EXTERNAL_READ Tools - May cost money but no side effects
# -----------------------------------------------------------------------------

register_tool(
    name="web_search",
    description="""Search the web for real-time information and get summarized results.

Use this when you need external information not available in the user's data:
- Industry benchmarks or best practices
- Company information not in the CRM
- Market trends or competitor analysis
- Current events or news about companies
- Sales methodologies or frameworks

Do NOT use this for data that's in the user's database - use run_sql_query instead.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query - be specific and include relevant context",
            },
        },
        "required": ["query"],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
)


register_tool(
    name="enrich_contacts_with_apollo",
    description="""Enrich contacts using Apollo.io's database to get current job titles, companies, and contact info.

Use this when users want to:
- Update outdated job titles and companies for contacts
- Fill in missing information like email, phone, LinkedIn
- Verify and refresh contact data quality

IMPORTANT: This requires Apollo.io integration and consumes credits.""",
    input_schema={
        "type": "object",
        "properties": {
            "contacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "domain": {"type": "string"},
                        "linkedin_url": {"type": "string"},
                        "organization_name": {"type": "string"},
                    },
                },
                "description": "List of contacts to enrich",
            },
            "reveal_personal_emails": {
                "type": "boolean",
                "description": "Request personal emails (uses additional credits)",
                "default": False,
            },
            "reveal_phone_numbers": {
                "type": "boolean",
                "description": "Request phone numbers (uses additional credits)",
                "default": False,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum contacts to enrich (default 50, max 500)",
                "default": 50,
            },
        },
        "required": ["contacts"],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
)


register_tool(
    name="enrich_company_with_apollo",
    description="""Enrich a company/organization using Apollo.io's database.

Get detailed company information like:
- Industry and sub-industry
- Employee count and revenue estimates
- Technologies used
- Company description and keywords

Requires company domain (e.g., "acme.com") to look up.""",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Company domain to enrich (e.g., 'acme.com')",
            },
        },
        "required": ["domain"],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
)


# -----------------------------------------------------------------------------
# EXTERNAL_WRITE Tools - Permanent external actions, approval by default
# -----------------------------------------------------------------------------

register_tool(
    name="send_email_from",
    description="""Send an email from your connected Gmail or Outlook account.

This sends as YOU, not as Revtops. The recipient will see the email from your address.
Use this for personalized outreach, follow-ups, or any email you'd normally send yourself.

The email will be sent from whichever email provider (Gmail or Outlook) you have connected.
If you haven't connected an email provider, this tool will not work.""",
    input_schema={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line",
            },
            "body": {
                "type": "string",
                "description": "Email body text (plain text)",
            },
            "cc": {
                "type": "array",
                "items": {"type": "string"},
                "description": "CC recipients (optional)",
            },
            "bcc": {
                "type": "array",
                "items": {"type": "string"},
                "description": "BCC recipients (optional)",
            },
        },
        "required": ["to", "subject", "body"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=True,
)


register_tool(
    name="send_slack",
    description="""Post a message to a Slack channel using your organization's Slack connection.

Use this to send notifications, updates, or alerts to team channels.
The message will appear as coming from the Revtops Slack app.

IMPORTANT - Slack uses mrkdwn, NOT standard Markdown:
- Bold: *text* (single asterisks, NOT **text**)
- Italic: _text_ (underscores)
- Strikethrough: ~text~
- Code: `code`
- Links: <https://url.com|link text>
- Bullet lists: Start lines with - or •

Examples:
- Post deal alerts to #sales-alerts
- Send weekly summaries to #revenue-team
- Notify about stale deals in #deal-reviews""",
    input_schema={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Channel name (e.g., '#sales-alerts') or channel ID",
            },
            "message": {
                "type": "string",
                "description": "Message text to post. Use Slack mrkdwn: *bold*, _italic_, ~strike~",
            },
            "thread_ts": {
                "type": "string",
                "description": "Thread timestamp to reply in thread (optional)",
            },
        },
        "required": ["channel", "message"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=True,
)


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
    name="trigger_workflow",
    description="""Manually trigger a workflow to run now.

Use this to test a workflow or run it on-demand outside its normal schedule.
The workflow will create a new conversation that you can view in the chat list.""",
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "UUID of the workflow to trigger",
            },
        },
        "required": ["workflow_id"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)


register_tool(
    name="run_workflow",
    description="""Execute another workflow and wait for its result.

Use this to compose workflows - a parent workflow can delegate tasks to specialist workflows.
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
    name="loop_over",
    description="""Execute a workflow for each item in a list.

Use this to process a batch of items (e.g., enrich 100 contacts, send 50 emails).
Each item is passed to the workflow as input_data.

Returns a summary with results and any failures.

Example: Query 100 contacts, then use loop_over to run "research-company" workflow for each.

Parameters:
- items: List of objects to process
- workflow_id: The workflow to run for each item
- max_concurrent: How many to run in parallel (default 3, max 10)
- max_items: Safety limit on total items (default 100, max 500)
- continue_on_error: If true, continue processing even if some items fail""",
    input_schema={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of items to process. Each item is passed to the workflow as input_data.",
            },
            "workflow_id": {
                "type": "string",
                "description": "UUID of the workflow to execute for each item",
            },
            "max_concurrent": {
                "type": "integer",
                "description": "Maximum parallel executions (default 3, max 10)",
                "default": 3,
            },
            "max_items": {
                "type": "integer",
                "description": "Maximum items to process (default 100, max 500)",
                "default": 100,
            },
            "continue_on_error": {
                "type": "boolean",
                "description": "Continue processing if an item fails (default true)",
                "default": True,
            },
        },
        "required": ["items", "workflow_id"],
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


def get_tools_for_claude() -> list[dict[str, Any]]:
    """Get tool definitions formatted for Claude's API."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in TOOL_DEFINITIONS.values()
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
