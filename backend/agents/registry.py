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
- goals: Revenue goals and quotas synced from CRM (name, target_amount, start_date, end_date, goal_type, owner_id, pipeline_id, source_system, source_id, custom_fields JSONB). Compare target_amount against deal totals to measure progress.
- integrations: Connected data sources (provider, is_active, last_sync_at)
- users: Team members (email, name, role)
- user_mappings_for_identity: Slack identity links (external_userid, external_email, match_source)
- organizations: User's company info (name, logo_url)
- github_repositories: Tracked GitHub repos (full_name, owner, name, is_tracked, last_sync_at). Join to commits/PRs via repository_id.
- github_commits: Commits on tracked repos (repository_id, sha, message, author_name, author_email, author_login, author_date, additions, deletions, user_id). Use organization_id in WHERE.
- github_pull_requests: PRs on tracked repos (repository_id, number, title, state, author_login, created_date, merged_date, additions, deletions, user_id). Use organization_id in WHERE.
- tracker_teams: Issue tracker teams/workspaces (source_system, source_id, name, key, description). Filter by source_system ('linear', 'jira', 'asana'). Join to issues via team_id.
- tracker_projects: Issue tracker projects (source_system, source_id, name, description, state, progress, target_date, start_date, lead_name, team_ids JSONB). Filter by source_system. Use organization_id in WHERE.
- tracker_issues: Issue tracker issues/tasks (source_system, source_id, team_id, identifier e.g. "ENG-123", title, description, state_name, state_type, priority 0-4, priority_label, issue_type, assignee_name, assignee_email, creator_name, project_id, labels JSONB, estimate, url, due_date, created_date, updated_date, completed_date, cancelled_date, user_id). Filter by source_system. Use organization_id in WHERE.
- shared_files: Synced file metadata from cloud sources like Google Drive (external_id, source, name, mime_type, folder_path, web_view_link, file_size, source_modified_at). Filter by organization_id AND user_id AND source (e.g. 'google_drive'). Use search_cloud_files tool instead for name-based searches.

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
    name="fetch_url",
    description="""Fetch the content of a web page by URL.

Use this when you need to read the actual content of a specific web page:
- Scrape a company's website, pricing page, or blog post
- Read a specific article or documentation page
- Extract structured data from a known URL
- Get the raw HTML of a page for analysis

By default this fetches directly (free, no proxy). Options:
- extract_text: Return clean extracted text instead of raw HTML (recommended for most use cases)
- render_js: Enable headless browser rendering for JS-heavy pages (uses ScrapingBee, costs credits)
- premium_proxy: Use residential proxy for sites that block datacenter IPs (uses ScrapingBee, costs credits)
- wait_ms: Wait time in ms after page load before capturing (only with render_js)

Only enable render_js or premium_proxy when actually needed — plain fetches are free.
For general web research where you don't have a specific URL, use web_search instead.""",
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch (must start with http:// or https://)",
            },
            "extract_text": {
                "type": "boolean",
                "description": "If true, return clean extracted text instead of raw HTML. Recommended for readability.",
                "default": True,
            },
            "render_js": {
                "type": "boolean",
                "description": "If true, render JavaScript using a headless browser. Use for SPAs and JS-heavy sites.",
                "default": False,
            },
            "premium_proxy": {
                "type": "boolean",
                "description": "If true, use residential proxy. Use for sites that block datacenter IPs (e.g. LinkedIn).",
                "default": False,
            },
            "wait_ms": {
                "type": "integer",
                "description": "Milliseconds to wait after page load before capturing (only with render_js=true, max 35000).",
            },
        },
        "required": ["url"],
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
    description="""Create or update records in the CRM (HubSpot) in bulk.

Use this when the user wants to add, update, or import contacts, companies, deals, or
engagement activities (calls, emails, meetings, notes).
This tool accepts a batch of records (up to 100) and routes them through a review workflow:
changes appear as "pending" in the Pending Changes panel where the user can Commit or Discard.

Property names for each record type:
- **contact**: email (required), firstname, lastname, company, jobtitle, phone
- **company**: name (required), domain, industry, numberofemployees
- **deal**: dealname (required), amount, dealstage, closedate, pipeline
- **call**: hs_timestamp (required), hs_call_title, hs_call_body, hs_call_duration (ms),
  hs_call_direction (INBOUND/OUTBOUND), hs_call_status (COMPLETED/NO_ANSWER/BUSY/etc),
  hs_call_disposition, hubspot_owner_id
- **email**: hs_timestamp (required), hs_email_subject, hs_email_text,
  hs_email_direction (EMAIL=outbound/INCOMING_EMAIL/FORWARDED_EMAIL),
  hs_email_status (SENT/BOUNCED/etc), hubspot_owner_id
- **meeting**: hs_timestamp (required), hs_meeting_title, hs_meeting_body,
  hs_meeting_start_time, hs_meeting_end_time, hs_meeting_location,
  hs_meeting_outcome (SCHEDULED/COMPLETED/RESCHEDULED/NO_SHOW/CANCELED), hubspot_owner_id
- **note**: hs_timestamp (required), hs_note_body (max 65536 chars), hubspot_owner_id

For engagements (call/email/meeting/note), each record can include an "associations" array
to link the activity to existing HubSpot records:
  "associations": [
    {"to_object_type": "contact", "to_object_id": "12345"},
    {"to_object_type": "deal", "to_object_id": "67890"}
  ]
Valid to_object_type values: contact, company, deal.
The to_object_id must be a HubSpot record ID (use crm_search to find IDs).

For updates (contacts/companies/deals only), each record MUST include an "id" field with the existing record UUID.

Example: create 3 contacts from a CSV:
{
  "target_system": "hubspot",
  "record_type": "contact",
  "operation": "create",
  "records": [
    {"email": "alice@acme.com", "firstname": "Alice", "lastname": "Smith", "company": "Acme"},
    {"email": "bob@acme.com", "firstname": "Bob", "lastname": "Jones", "company": "Acme"},
    {"email": "carol@acme.com", "firstname": "Carol", "lastname": "Lee", "company": "Acme"}
  ]
}

Example: log a call on a deal:
{
  "target_system": "hubspot",
  "record_type": "call",
  "operation": "create",
  "records": [
    {
      "hs_timestamp": "2025-03-17T10:30:00Z",
      "hs_call_title": "Discovery Call",
      "hs_call_body": "Discussed product needs and timeline",
      "hs_call_duration": "1800000",
      "hs_call_direction": "OUTBOUND",
      "hs_call_status": "COMPLETED",
      "associations": [{"to_object_type": "deal", "to_object_id": "67890"}]
    }
  ]
}

IMPORTANT: Always explain what you're going to create/update BEFORE calling this tool.
Do NOT add any text after the tool call - let the pending changes panel speak for itself.""",
    input_schema={
        "type": "object",
        "properties": {
            "target_system": {
                "type": "string",
                "enum": ["hubspot"],
                "description": "Target CRM system",
            },
            "record_type": {
                "type": "string",
                "enum": ["contact", "company", "deal", "call", "email", "meeting", "note"],
                "description": "Type of CRM record or engagement",
            },
            "operation": {
                "type": "string",
                "enum": ["create", "update"],
                "description": "Whether to create new records or update existing ones. Engagements only support 'create'.",
            },
            "records": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Array of record objects (max 100). For updates, each must include 'id'. For engagements, each can include 'associations'.",
            },
        },
        "required": ["target_system", "record_type", "operation", "records"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=False,  # Has its own review flow via ChangeSession
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
    name="github_issues_access",
    description="""Create a GitHub issue in a repository your organization has connected.

Use this when the user asks to file/report issues in GitHub.

Required:
- repo_full_name: Repository in owner/repo format (e.g. 'octocat/Hello-World')
- title: Issue title

Optional:
- body: Markdown body content for the issue
- labels: List of label names
- assignees: List of GitHub usernames to assign

This only changes GitHub issues (never source code). It is an external write action and should be reviewed before sending unless auto-approved.""",
    input_schema={
        "type": "object",
        "properties": {
            "repo_full_name": {
                "type": "string",
                "description": "Repository in owner/repo format (e.g., 'octocat/Hello-World')",
            },
            "title": {
                "type": "string",
                "description": "Issue title",
            },
            "body": {
                "type": "string",
                "description": "Issue body in Markdown (optional)",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Label names to apply (optional)",
            },
            "assignees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "GitHub usernames to assign (optional)",
            },
        },
        "required": ["repo_full_name", "title"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=True,
)


register_tool(
    name="create_linear_issue",
    description="""Create an issue in Linear (issue tracking).

Use this when the user asks to create a ticket, task, or issue in Linear.

Required:
- team_key: Team key (e.g. 'ENG', 'PROD'). Use run_sql_query on tracker_teams WHERE source_system='linear' to find valid keys.
- title: Issue title

Optional:
- description: Markdown body for the issue
- priority: 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low
- assignee_name: Display name of the assignee (resolved to Linear user)
- project_name: Project name to add the issue to
- labels: List of label names

This is an external write action and requires approval unless auto-approved.""",
    input_schema={
        "type": "object",
        "properties": {
            "team_key": {
                "type": "string",
                "description": "Team key (e.g. 'ENG'). Query tracker_teams WHERE source_system='linear' for valid keys.",
            },
            "title": {
                "type": "string",
                "description": "Issue title",
            },
            "description": {
                "type": "string",
                "description": "Issue body in Markdown (optional)",
            },
            "priority": {
                "type": "integer",
                "description": "Priority: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low",
                "enum": [0, 1, 2, 3, 4],
            },
            "assignee_name": {
                "type": "string",
                "description": "Display name of the person to assign (optional)",
            },
            "project_name": {
                "type": "string",
                "description": "Project name to attach this issue to (optional)",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Label names to apply (optional)",
            },
        },
        "required": ["team_key", "title"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=True,
)


register_tool(
    name="update_linear_issue",
    description="""Update an existing issue in Linear.

Use this when the user asks to change an issue's title, description, state, priority, or assignee.

Required:
- issue_identifier: The issue identifier like 'ENG-123'. Query tracker_issues WHERE source_system='linear' to find identifiers.

All other fields are optional — only provide the ones to change:
- title: New title
- description: New description (Markdown)
- state_name: New state (e.g. 'In Progress', 'Done'). Must match a valid workflow state for the issue's team.
- priority: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low
- assignee_name: Display name of the new assignee

This is an external write action and requires approval unless auto-approved.""",
    input_schema={
        "type": "object",
        "properties": {
            "issue_identifier": {
                "type": "string",
                "description": "Issue identifier like 'ENG-123'",
            },
            "title": {
                "type": "string",
                "description": "New title (optional)",
            },
            "description": {
                "type": "string",
                "description": "New description in Markdown (optional)",
            },
            "state_name": {
                "type": "string",
                "description": "New state name, e.g. 'In Progress', 'Done' (optional)",
            },
            "priority": {
                "type": "integer",
                "description": "Priority: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low",
                "enum": [0, 1, 2, 3, 4],
            },
            "assignee_name": {
                "type": "string",
                "description": "Display name of the new assignee (optional)",
            },
        },
        "required": ["issue_identifier"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=True,
)


register_tool(
    name="search_linear_issues",
    description="""Search issues in Linear in real-time (not just synced data).

Use this when the user wants to find issues by keyword, filter by team, or get the latest status
from Linear directly. This calls the Linear API live, so results are always up-to-date.

For querying synced/historical data, use run_sql_query on tracker_issues WHERE source_system='linear' instead.

Examples:
- "Search Linear for authentication bugs"
- "Find open issues assigned to Alice in ENG"
- "Look for issues about the onboarding flow" """,
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query text",
            },
            "team_key": {
                "type": "string",
                "description": "Optional team key to filter (e.g. 'ENG')",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20, max 50)",
                "default": 20,
            },
        },
        "required": ["query"],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
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
# Cloud File Tools (Google Drive, Airtable, OneDrive, etc.)
# -----------------------------------------------------------------------------

register_tool(
    name="search_cloud_files",
    description="""Search the user's synced cloud files by name.

Searches files synced from any connected source (Google Drive, Airtable, OneDrive, etc.).
Returns matching file metadata including name, MIME type, folder path, source, and external_id.
Use the returned external_id with read_cloud_file to get the text content.

Use '*' as the name_query to list all files (most recently modified first).
Optionally filter by source (e.g. 'google_drive') if the user specifies a particular service.

NOTE: Files must have been synced first. If no results are found, suggest the user
sync from the Data Sources page.

Examples:
- "Find the Q4 planning doc"
- "Search for spreadsheets with 'revenue' in the name"
- "Show me all my synced files"
""",
    input_schema={
        "type": "object",
        "properties": {
            "name_query": {
                "type": "string",
                "description": "File name to search for (case-insensitive substring match). Use '*' to list all files.",
            },
            "source": {
                "type": "string",
                "description": "Optional: filter by source (e.g. 'google_drive'). Omit to search all sources.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 20)",
                "default": 20,
            },
        },
        "required": ["name_query"],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=False,
)


register_tool(
    name="read_cloud_file",
    description="""Read the text content of a synced cloud file.

Extracts text from files based on their source:
- Google Docs → plain text
- Google Sheets → CSV (all sheets combined)
- Google Slides → plain text
- Other text-based files → raw text

Use search_cloud_files first to find the file's external_id, then use this tool
to read its content into the conversation context.

Content is truncated at ~100K characters for very large files.""",
    input_schema={
        "type": "object",
        "properties": {
            "external_id": {
                "type": "string",
                "description": "The file's external_id (from search_cloud_files results)",
            },
        },
        "required": ["external_id"],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
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
    default_requires_approval=True,
)

register_tool(
    name="save_memory",
    description="""Save a memory or preference for the current user that will be recalled at the start of every future conversation.

Use this when the user explicitly asks you to "remember" something, or states a preference they want persisted (e.g. "always be concise", "my territory is EMEA", "I prefer tables over lists").

Each memory should be a single, self-contained statement. Save multiple memories as separate calls if the user gives you several things to remember.

Do NOT save conversation-specific context (like "user asked about deal X") — only persistent preferences and facts.""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The memory to save. A concise, self-contained statement (e.g. 'User prefers concise answers' or 'User manages the EMEA territory').",
            },
        },
        "required": ["content"],
    },
    category=ToolCategory.LOCAL_WRITE,
    default_requires_approval=True,
)

register_tool(
    name="delete_memory",
    description="""Delete a previously saved memory for the current user.

Use this when the user asks you to forget something, or when a previously saved memory is no longer relevant. You must provide the exact memory_id (UUID) from the memories listed in the system prompt.""",
    input_schema={
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "UUID of the memory to delete.",
            },
        },
        "required": ["memory_id"],
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
