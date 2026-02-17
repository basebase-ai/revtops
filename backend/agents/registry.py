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
- activities: Raw activity records - query by TYPE not source
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
- shared_files: Synced file metadata from cloud sources like Google Drive (external_id, source, name, mime_type, folder_path, web_view_link, file_size, source_modified_at). Filter by source (e.g. 'google_drive'). Use search_cloud_files tool instead for name-based searches.
- temp_data: Agent-generated results and computed metrics. Flexible JSONB storage linked to entities. Columns: entity_type, entity_id, namespace, key, value (JSONB), metadata (JSONB), created_by_user_id, created_at, expires_at. Example: SELECT td.value->>'score' as confidence, d.name FROM temp_data td JOIN deals d ON d.id = td.entity_id WHERE td.namespace = 'deal_confidence'

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


register_tool(
    name="execute_command",
    description="""Run a shell command in a persistent Linux sandbox.

The sandbox is a full Debian Linux environment that persists across calls within
this conversation. You can install tools, write files, run scripts, and build up
work iteratively — just like using a terminal.

Pre-installed: python3, pip, node, common CLI tools.
Database: A read-only PostgreSQL connection is available at $DATABASE_URL.
A helper is pre-loaded at /home/user/db.py — use `from db import get_connection`
to get a psycopg2 connection with the correct organization scope.

Use this for:
- Complex data analysis that goes beyond a single SQL query
- Writing and executing scripts (Python, bash, Node.js, etc.)
- Installing and using CLI tools or libraries
- Multi-step computations where you iterate on results
- Generating charts, files, or reports programmatically

Examples:
  "pip install pandas matplotlib"
  "python3 -c \\"import pandas as pd; from db import get_connection; df = pd.read_sql('SELECT * FROM deals', get_connection()); print(df.describe())\\""
  "cat > analysis.py << 'EOF'\\nimport pandas as pd\\n...\\nEOF"
  "python3 analysis.py"

Files saved to /home/user/output/ are returned as downloadable artifacts.""",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute in the sandbox",
            },
        },
        "required": ["command"],
    },
    category=ToolCategory.LOCAL_READ,
    default_requires_approval=True,
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

Do NOT use this for data that's in the user's database - use run_sql_query instead.

Provider choice (default exa): Use exa for semantic search over the live web with per-result excerpts (title, url, content snippets) — best when you need to compare or cite specific pages. Use perplexity when you want a single synthesized answer in one blob with citation URLs and no per-result text.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query - be specific and include relevant context",
            },
            "provider": {
                "type": "string",
                "enum": ["perplexity", "exa"],
                "description": "Search provider: exa (default) = semantic search, per-result excerpts; perplexity = single synthesized answer with citation URLs. Prefer exa for comparing/citing pages; perplexity for one-shot answers.",
                "default": "exa",
            },
            "num_results": {
                "type": "integer",
                "description": "Max number of results (Exa only; default 10). Ignored for perplexity.",
                "default": 10,
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
    name="enrich_with_apollo",
    description="""Enrich contacts or a company using Apollo.io's database.

Set type to "contacts" (default) to enrich people, or "company" to enrich a single company by domain.

For contacts: updates job titles, companies, emails, phones, LinkedIn URLs.
For companies: returns industry, employee count, revenue, technologies, description.

Requires Apollo.io integration and consumes credits.""",
    input_schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["contacts", "company"],
                "description": "What to enrich: 'contacts' for people, 'company' for a single company.",
                "default": "contacts",
            },
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
                "description": "List of contacts to enrich (when type='contacts')",
            },
            "domain": {
                "type": "string",
                "description": "Company domain to enrich (when type='company', e.g. 'acme.com')",
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
        "required": [],
    },
    category=ToolCategory.EXTERNAL_READ,
    default_requires_approval=False,
)


# -----------------------------------------------------------------------------
# EXTERNAL_WRITE Tools - Permanent external actions, approval by default
# -----------------------------------------------------------------------------

register_tool(
    name="write_to_system_of_record",
    description="""Create or update records in any connected system of record.

This is the universal tool for writing to external systems — CRMs, issue trackers,
code repositories, and any other connected integration. Use it whenever the user asks
to create or update records in an external system.

Supported systems and record types:

**CRM (hubspot):**
- record_type "contact": email (required), firstname, lastname, company, jobtitle, phone
- record_type "company": name (required), domain, industry, numberofemployees
- record_type "deal": dealname (required), amount, dealstage, closedate, pipeline
- record_type "call": hs_timestamp (required), hs_call_title, hs_call_body, hs_call_duration (ms),
  hs_call_direction (INBOUND/OUTBOUND), hs_call_status (COMPLETED/NO_ANSWER/BUSY/etc),
  hs_call_disposition, hubspot_owner_id
- record_type "email": hs_timestamp (required), hs_email_subject, hs_email_text,
  hs_email_direction (EMAIL=outbound/INCOMING_EMAIL/FORWARDED_EMAIL),
  hs_email_status (SENT/BOUNCED/etc), hubspot_owner_id
- record_type "meeting": hs_timestamp (required), hs_meeting_title, hs_meeting_body,
  hs_meeting_start_time, hs_meeting_end_time, hs_meeting_location,
  hs_meeting_outcome (SCHEDULED/COMPLETED/RESCHEDULED/NO_SHOW/CANCELED), hubspot_owner_id
- record_type "note": hs_timestamp (required), hs_note_body (max 65536 chars), hubspot_owner_id
For engagements (call/email/meeting/note), each record can include "associations":
  [{"to_object_type": "contact", "to_object_id": "12345"}, ...]
For updates (contacts/companies/deals only), each record MUST include an "id" field.
Bulk CRM writes (multiple records) go through the Pending Changes panel for review.

**Issue trackers (linear, jira, asana):**
- record_type "issue", operation "create": team_key (required), title (required),
  description, priority (0-4), assignee_name, project_name, labels
- record_type "issue", operation "update": issue_identifier (required, e.g. 'ENG-123'),
  title, description, state_name, priority (0-4), assignee_name
  Query tracker_teams WHERE source_system='linear' for valid team keys.
  Query tracker_issues for valid issue identifiers.

**Code repositories (github, gitlab):**
- record_type "issue", operation "create": repo_full_name (required, 'owner/repo'),
  title (required), body, labels, assignees

Single-record writes execute immediately. Bulk CRM writes (>5 records) create pending changes.
Set require_review=true to ALWAYS route through Pending Changes for human review,
regardless of record count. Use this in workflows where accuracy matters.

Example — create a Linear issue:
{
  "target_system": "linear",
  "record_type": "issue",
  "operation": "create",
  "records": [{"team_key": "ENG", "title": "Fix login bug", "priority": 2}]
}

Example — create HubSpot contacts:
{
  "target_system": "hubspot",
  "record_type": "contact",
  "operation": "create",
  "records": [
    {"email": "alice@acme.com", "firstname": "Alice", "lastname": "Smith"},
    {"email": "bob@acme.com", "firstname": "Bob", "lastname": "Jones"}
  ]
}

IMPORTANT: Always explain what you're going to create/update BEFORE calling this tool.""",
    input_schema={
        "type": "object",
        "properties": {
            "target_system": {
                "type": "string",
                "description": "Target system to write to (e.g. 'hubspot', 'linear', 'github', 'jira', 'salesforce', 'gitlab', 'asana')",
            },
            "record_type": {
                "type": "string",
                "description": "Type of record (e.g. 'contact', 'company', 'deal', 'issue', 'call', 'email', 'meeting', 'note')",
            },
            "operation": {
                "type": "string",
                "enum": ["create", "update"],
                "description": "Whether to create new records or update existing ones",
            },
            "records": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Array of record objects. Fields vary by target_system and record_type. Max 100 for CRM systems.",
            },
            "require_review": {
                "type": "boolean",
                "description": "If true, always route through Pending Changes for human review instead of writing directly. Use for bulk enrichment or any workflow where a human should verify changes before they go live.",
            },
        },
        "required": ["target_system", "record_type", "operation", "records"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
    default_requires_approval=False,
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
    default_requires_approval=False,
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
    default_requires_approval=False,
)


register_tool(
    name="send_sms",
    description="""Send an SMS text message to a phone number via Twilio.

Use this to text the user or any phone number directly. Messages are sent from the Revtops system number.

The recipient number must be in E.164 format (e.g. +14155551234). If the user says "text me" or "send me a text", look up their phone_number from the users table first.

Max message length is 1600 characters.""",
    input_schema={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient phone number in E.164 format (e.g. +14155551234)",
            },
            "body": {
                "type": "string",
                "description": "Message text (max 1600 characters)",
            },
        },
        "required": ["to", "body"],
    },
    category=ToolCategory.EXTERNAL_WRITE,
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
  Example: foreach(tool="web_search", items_query="SELECT id, name FROM contacts", params_template={"query": "Current role of {{name}}?"})

**Workflow mode** — each item dict is passed as input_data to the workflow. Uses async in-process execution with context propagation.
  Example: foreach(workflow_id="uuid-...", items=[{"email": "a@b.com"}, {"email": "c@d.com"}])

Set max_concurrent=1 for sequential execution, higher for parallel. Blocks until all items complete, streaming live progress to the UI.""",
    input_schema={
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Name of the tool to run per item (e.g., 'web_search', 'fetch_url', 'enrich_with_apollo'). Mutually exclusive with workflow_id.",
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
