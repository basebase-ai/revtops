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
    name="create_artifact",
    description="""Save an analysis, report, or dashboard for the user to view later.

Use this when you've created something the user might want to reference again:
- A pipeline analysis dashboard
- A quarterly report summary
- A list of at-risk deals with recommendations""",
    input_schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["dashboard", "report", "analysis"],
                "description": "Type of artifact to create",
            },
            "title": {
                "type": "string",
                "description": "Title of the artifact",
            },
            "description": {
                "type": "string",
                "description": "Description of what this artifact contains",
            },
            "data": {
                "type": "object",
                "description": "The analysis data/content",
            },
            "is_live": {
                "type": "boolean",
                "description": "Whether to refresh data on load (true) or keep static (false)",
                "default": False,
            },
        },
        "required": ["type", "title", "data"],
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
    name="crm_write",
    description="""Create or update records in the CRM (HubSpot).

Records are created locally first (like editing files). The user can then:
- Click "Commit All" to sync to HubSpot
- Click "Undo All" to discard the local changes

Use this for:
- Creating contacts from prospect lists
- Creating companies from account data
- Creating deals from opportunity information
- Updating existing records

Property names for each record type:
- contact: email (required), firstname, lastname, company, jobtitle, phone
- company: name (required), domain, industry, numberofemployees
- deal: dealname (required), amount, dealstage, closedate, pipeline""",
    input_schema={
        "type": "object",
        "properties": {
            "target_system": {
                "type": "string",
                "enum": ["hubspot"],
                "description": "The CRM system to write to",
            },
            "record_type": {
                "type": "string",
                "enum": ["contact", "company", "deal"],
                "description": "Type of CRM record to create/update",
            },
            "operation": {
                "type": "string",
                "enum": ["create", "update", "upsert"],
                "description": "Operation to perform",
            },
            "records": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Array of records to write",
            },
        },
        "required": ["target_system", "record_type", "operation", "records"],
    },
    category=ToolCategory.LOCAL_WRITE,  # Now local-first, commit later
    default_requires_approval=False,  # No approval needed - use bottom panel to commit/undo
)


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
                "description": "Message text to post",
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
    name="create_workflow",
    description="""Create a workflow automation that runs on a schedule or in response to events.

Workflows are prompts sent to the agent on a schedule. The agent will use tools to 
accomplish the task. You can specify which tools the workflow can use without approval.

Example workflow:
- Name: "Daily Stale Deals Alert"
- Prompt: "Find deals without activity in 30 days, summarize top 5, post to #sales-alerts"
- Auto-approve: ["send_slack"]
- Trigger: Schedule, "0 9 * * 1-5" (weekdays at 9am)

If a tool requires approval and isn't in auto_approve_tools, the workflow will pause
and wait for user approval (visible in the chat interface).""",
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Human-readable name for the workflow",
            },
            "description": {
                "type": "string",
                "description": "Description of what the workflow does",
            },
            "prompt": {
                "type": "string",
                "description": "Instructions for the agent to follow when the workflow runs",
            },
            "trigger_type": {
                "type": "string",
                "enum": ["schedule", "event", "manual"],
                "description": "What triggers this workflow",
            },
            "trigger_config": {
                "type": "object",
                "description": "Trigger configuration. For schedule: {cron: '0 9 * * *'}. For event: {event: 'sync.completed'}",
            },
            "auto_approve_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tools that can run without approval for this workflow",
            },
        },
        "required": ["name", "prompt", "trigger_type", "trigger_config"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=False,
)


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
