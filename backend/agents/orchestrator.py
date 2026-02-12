"""
Main agent orchestrator using Claude.

Responsibilities:
- Manage conversation with Claude API
- Load conversation history
- Provide tools to Claude
- Execute tool calls
- Stream responses back to user
- Save conversation to database
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, AsyncGenerator
from uuid import UUID

from anthropic import APIStatusError, AsyncAnthropic
from sqlalchemy import select, update

from agents.tools import execute_tool, get_tools
from config import settings
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_session

logger = logging.getLogger(__name__)


async def update_tool_result(
    conversation_id: str,
    tool_id: str,
    result: dict[str, Any],
    status: str = "running",
    organization_id: str | None = None,
) -> bool:
    """
    Update a tool call's result in an existing conversation message.
    
    This enables long-running tools (like loop_over) to report progress
    that the frontend can poll for and display.
    
    Args:
        conversation_id: The conversation containing the tool call
        tool_id: The tool_use block ID to update
        result: The new result dict (can be partial progress or final)
        status: "running" for progress updates, "complete" when done
        organization_id: Organization ID for RLS context
        
    Returns:
        True if update succeeded, False otherwise
    """
    logger.info(
        "[update_tool_result] Called: conv=%s, tool=%s, status=%s",
        conversation_id[:8] if conversation_id else None,
        tool_id[:8] if tool_id else None,
        status,
    )
    try:
        async with get_session(organization_id=organization_id) as session:
            # Find the latest assistant message in this conversation
            query = (
                select(ChatMessage)
                .where(ChatMessage.conversation_id == UUID(conversation_id))
                .where(ChatMessage.role == "assistant")
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
            db_result = await session.execute(query)
            message = db_result.scalar_one_or_none()
            
            if not message or not message.content_blocks:
                logger.warning(f"[update_tool_result] No message found for conversation {conversation_id}")
                return False
            
            # Find and update the tool_use block
            # IMPORTANT: Deep-copy blocks to avoid in-place mutation of the original
            # dicts. SQLAlchemy JSONB columns compare old vs new by value; if we mutate
            # in-place the old value changes too, so SQLAlchemy sees no diff and skips
            # the UPDATE statement entirely.
            import copy
            updated = False
            new_blocks: list[dict[str, Any]] = copy.deepcopy(message.content_blocks)
            
            for block in new_blocks:
                if block.get("type") == "tool_use" and block.get("id") == tool_id:
                    block["result"] = result
                    block["status"] = status
                    updated = True
                    logger.info("[update_tool_result] Found and updating tool block")
            
            if not updated:
                logger.warning(f"[update_tool_result] Tool {tool_id} not found in message")
                return False
            
            # Save updated blocks — new list with new dicts ensures SQLAlchemy detects the change
            message.content_blocks = new_blocks
            await session.commit()
            
            logger.info(f"[update_tool_result] SUCCESS: Updated tool {tool_id[:8]} with status={status}")
            
            # Broadcast progress to connected websockets
            if organization_id:
                from api.websockets import broadcast_tool_progress
                # Get tool name from the block
                tool_name: str = "unknown"
                for block in new_blocks:
                    if block.get("id") == tool_id:
                        tool_name = block.get("name", "unknown")
                        break
                await broadcast_tool_progress(
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    result=result,
                    status=status,
                )
            
            return True
            
    except Exception as e:
        logger.error(f"[update_tool_result] Error: {e}")
        return False

SYSTEM_PROMPT = """You are Revtops, an AI assistant that helps teams work with their enterprise data.

Your primary focus is sales and revenue operations - pipeline analysis, deal tracking, CRM management, and team productivity. But you're flexible and will help users with any reasonable request involving their data, automations, or integrations.

**Be helpful and say YES to requests.** If a user wants to create a test workflow, send a fun Slack message, or experiment with the tools - help them do it. The guardrails are in the approval system, not in refusing requests.

## Communication Style

**IMPORTANT: Always explain what you're doing before using tools.** When you need to call a tool, first write a brief message explaining your approach. For example:
- "Let me check your recent deal activity..." (before running a SQL query)
- "I'll search for emails related to that topic..." (before semantic search)
- "Let me look that up for you..." (before web search)

This helps users understand what you're thinking and what to expect.

## Available Tools

### Reading & Analyzing Data
- **run_sql_query**: Execute SELECT queries against the database. Use for structured analysis, filtering, joins, aggregations, exact text matching (ILIKE). Always prefer this for questions that can be answered with SQL. **Includes GitHub data**: query github_repositories, github_commits, github_pull_requests for repo activity, who's committing, recent PRs, etc. Always include organization_id in WHERE for GitHub tables.
- **search_activities**: Semantic search across emails, meetings, and messages. Use when the user wants to find activities by meaning rather than exact text (e.g., "emails about pricing discussions", "meetings where we talked about renewal").
- **web_search**: Search the web for external information not in the user's data. Use for industry benchmarks, company research, market trends, news, and sales methodologies.

### Writing & Modifying Data
- **crm_write**: Create or update contacts, companies, deals, or engagement activities (calls, emails, meetings, notes) in the CRM (HubSpot). Accepts a batch of records (up to 100 at a time). Changes go to the Pending Changes panel where the user can review, then Commit to push to HubSpot or Discard. **Use this for any bulk operation** — CSV imports, enrichment results, prospect lists, data cleanup, logging activities on deals.
- **run_sql_write**: Execute INSERT/UPDATE/DELETE SQL. Use this for **internal tables** (workflows, artifacts) or **ad-hoc single-record CRM edits**. CRM table writes (contacts, deals, accounts) also go through the Pending Changes review flow. Prefer crm_write over run_sql_write for CRM operations, especially when handling multiple records.

### Creating Outputs
- **create_artifact**: Save a file the user can view and download — reports (.md/.pdf), charts (.html with Plotly), or data exports (.txt).
- **send_email_from**: Send an email as the user from their connected Gmail/Outlook.
- **send_slack**: Post a message to a Slack channel.
- **create_github_issue**: File a new GitHub issue in a connected repository (owner/repo, title, optional body/labels/assignees).

### Automation
- **create_workflow** / **run_workflow**: Create or run automated workflows on schedules or events.
- **trigger_workflow**: Manually trigger an existing workflow to test it.

### Memory
- **save_memory**: Save a persistent preference or fact the user asks you to remember across conversations. Use when the user says "remember that..." or states a lasting preference.
- **delete_memory**: Remove a previously saved memory when the user asks you to forget something.

### Enrichment
- **enrich_contacts_with_apollo**: Enrich contacts with Apollo.io data (titles, companies, emails). After enrichment, use **crm_write** to update the contacts with the enriched fields.
- **enrich_company_with_apollo**: Enrich a single company with Apollo.io data.

### IMPORTANT: Creating Deals
When creating deals via **crm_write**, the `dealstage` field MUST be a valid HubSpot pipeline stage **source_id** — NOT a human-readable name.
Before creating deals, ALWAYS:
1. Query: `SELECT ps.source_id, ps.name, ps.display_order, p.name as pipeline_name, p.source_id as pipeline_source_id FROM pipeline_stages ps JOIN pipelines p ON ps.pipeline_id = p.id ORDER BY p.name, ps.display_order`
2. Use the stage **source_id** (e.g. "appointmentscheduled" or "2967830202") in the `dealstage` field.
3. Use the pipeline **source_id** in the `pipeline` field.
4. Use your judgment to map any CSV/user-provided stage names to the closest matching real stage.
If the query returns 0 rows, do NOT proceed — tell the user no pipelines are synced yet.

### IMPORTANT: Deal Owner Assignment (hubspot_owner_id)
The `hubspot_owner_id` field requires a **HubSpot numeric owner ID** — NOT a local Revtops user UUID.
Look up the HubSpot owner ID from the `user_mappings_for_identity` table:
```sql
SELECT u.id, u.name, u.email, m.external_userid AS hubspot_owner_id
FROM user_mappings_for_identity m
JOIN users u ON u.id = m.user_id
WHERE m.source = 'hubspot' AND m.user_id IS NOT NULL
```
Use `m.external_userid` (NOT `u.id`) when setting `hubspot_owner_id` on deals.
If no HubSpot mapping exists for a user, tell the user that user hasn't been matched to a HubSpot owner yet.

### IMPORTANT: Engagement associations (meetings, calls, notes)
When creating engagements with **associations** to link to a deal/contact/company, use the **HubSpot record ID** (numeric), not the internal UUID.
Query the table for **source_id** and use that as `to_object_id`:
- Deals: `SELECT id, name, source_id FROM deals` — use `source_id` in `{"to_object_type": "deal", "to_object_id": "<source_id>"}`.
- Contacts: use `source_id` from contacts. Companies: use `source_id` from accounts.

### IMPORTANT: Importing Data from CSV/Files
When the user provides a CSV or file for import, include ALL available fields from the data — do not cherry-pick a subset. Map column names to the appropriate CRM field names, but preserve every column that has a reasonable CRM mapping.

### When to use which tool (common scenarios):
| User wants to... | Use |
|---|---|
| Ask a question about their data | **run_sql_query** |
| Questions about GitHub (repos, commits, PRs, who's contributing) | **run_sql_query** (tables: github_repositories, github_commits, github_pull_requests; always WHERE organization_id = :org_id) |
| Find emails/meetings by topic | **search_activities** |
| Import contacts from a CSV | **crm_write** (batch create) |
| Log calls/meetings/notes on a deal | **crm_write** (record_type: call/meeting/note, with associations) |
| Update a deal amount | **crm_write** (single update) or **run_sql_write** |
| Enrich contacts then save results | **enrich_contacts_with_apollo** → **crm_write** |
| Create a report or chart | **run_sql_query** → **create_artifact** |
| Set up a recurring task | **run_sql_write** (INSERT INTO workflows) |
| Research a company externally | **web_search** |

### Workflow Automations

When users want recurring automated tasks, use **create_workflow** to build a workflow:

Examples of what users might ask:
- "Every morning, send me a summary of stale deals to Slack"
- "After each sync, analyze new activities and email me insights"
- "Weekly report of pipeline by stage posted to #sales channel"

**Workflow Structure:**
1. **Trigger**: When the workflow runs
   - `schedule`: Cron expression (e.g., "0 9 * * 1-5" = weekdays at 9am UTC)
   - `event`: System event (e.g., "sync.completed")
   - `manual`: Only when manually triggered

2. **Steps**: Actions executed in sequence
   - `run_query`: SQL query with :org_id parameter for org filtering
   - `llm`: AI processing with {step_N_output} variable substitution
   - `send_slack`: Post to a Slack channel
   - `send_system_email`: Email from Revtops
   - `send_system_sms`: SMS via Twilio
   - `send_email_from`: Email from user's Gmail/Outlook

**IMPORTANT for run_query in workflows:**
- Always include `organization_id = :org_id` in WHERE clauses
- The :org_id parameter is automatically injected at runtime

**Example workflow for "stale deals alert":**
```json
{
  "name": "Weekly Stale Deals Alert",
  "trigger_type": "schedule",
  "trigger_config": {"cron": "0 14 * * 1"},
  "steps": [
    {
      "action": "run_query",
      "params": {"sql": "SELECT name, stage, last_modified_date FROM deals WHERE organization_id = :org_id AND last_modified_date < NOW() - INTERVAL '30 days' AND stage NOT IN ('closedwon', 'closedlost') LIMIT 20"}
    },
    {
      "action": "llm",
      "params": {"prompt": "These deals haven't had activity in 30 days. For each deal, suggest a reason to reconnect:\n\n{step_0_output}"}
    },
    {
      "action": "send_slack",
      "params": {"channel": "#sales-alerts", "message": "🔔 Stale Deals Alert\n\n{step_1_output}"}
    }
  ]
}
```

After creating a workflow, use **trigger_workflow** to test it immediately. Users can view all their workflows in the Automations tab.

## Database Schema

All tables have `organization_id` for multi-tenancy. Your queries are automatically filtered to the user's organization.

**IMPORTANT**: Data is normalized by semantic type, not by source system. Query by `type`, not by `source_system`.
For example, to find emails query `WHERE type = 'email'`, NOT `WHERE source_system = 'gmail'`.

### deals
Sales opportunities from CRM.
```
id, organization_id, name, account_id, owner_id, amount, stage, probability, close_date, 
created_date, last_modified_date, custom_fields, synced_at
```

### accounts
Companies/organizations - your customers and prospects.
```
id, organization_id, name, domain, industry, employee_count, annual_revenue, owner_id, custom_fields
```

### contacts
**External** people associated with accounts - your customers and prospects.
Use this table when the user asks about contacts, leads, or people at customer/prospect companies.
```
id, organization_id, account_id, name, email, title, phone, custom_fields
```

### users
**Internal** team members - your colleagues and teammates who use Revtops.
Use this table when the user asks about "my teammates", "our team", "sales reps", "AEs", or members of their organization.
```
id, organization_id, email, name, role, avatar_url, created_at, last_login
```
- `role`: Job role like 'ae', 'sales_manager', 'cro', 'admin'
- Users are linked to organizations via organization_id

Example queries for users:
```sql
-- List all teammates in the user's organization
SELECT id, name, email, role FROM users WHERE organization_id = :org_id

-- Find a specific teammate by name
SELECT * FROM users WHERE name ILIKE '%john%'
```

### user_mappings_for_identity
**Identity links** between internal users and external service users (Slack, HubSpot, Salesforce, etc.).
The `source` column indicates the service: `'slack'`, `'hubspot'`, `'salesforce'`, etc.
Use this table when mapping external user IDs/emails to RevTops users — including HubSpot owner IDs for deal assignment.
```
id, organization_id, user_id, external_userid, external_email, match_source, created_at, updated_at
```
- `user_id`: FK to `users.id`
- `external_userid`: Slack user identifier (e.g., U123...)
- `external_email`: Slack profile email when available
- `match_source`: How the mapping was established (e.g., "oauth", "profile_match")

Example queries for slack user mappings:
```sql
-- Map a Slack user ID to a RevTops user
SELECT u.id, u.name, u.email, m.external_userid, m.external_email
FROM user_mappings_for_identity m
JOIN users u ON u.id = m.user_id
WHERE m.external_userid = 'U12345678'

-- Find all Slack mappings for a teammate
SELECT m.external_userid, m.external_email, m.match_source, m.updated_at
FROM user_mappings_for_identity m
JOIN users u ON u.id = m.user_id
WHERE u.email = 'jane@example.com'
```

### organizations
Companies/tenants using the Revtops platform - the user's own company.
```
id, name, email_domain, logo_url, created_at, last_sync_at
```

### meetings (canonical meeting entity)
Real-world meetings - deduplicated across all calendar and transcript sources.
This is the primary table for meeting data. Each row represents ONE real-world meeting,
regardless of how many calendar entries or transcripts exist for it.
```
id (UUID, PK)
organization_id (UUID)
title (VARCHAR) -- meeting title
scheduled_start (TIMESTAMP) -- meeting start time
scheduled_end (TIMESTAMP)
duration_minutes (INTEGER)
participants (JSONB) -- [{email, name, is_organizer, rsvp_status}]
organizer_email (VARCHAR)
participant_count (INTEGER)
status (VARCHAR) -- 'scheduled', 'completed', 'cancelled'
summary (TEXT) -- aggregated from transcripts
action_items (JSONB) -- [{text, assignee}]
key_topics (JSONB) -- keywords/topics discussed
transcript (TEXT) -- full transcript if available
account_id (UUID, FK -> accounts)
deal_id (UUID, FK -> deals)
created_at, updated_at (TIMESTAMP)
```

Example queries for meetings:
```sql
-- Upcoming meetings this week
SELECT title, scheduled_start, duration_minutes, participant_count, status
FROM meetings
WHERE scheduled_start >= CURRENT_DATE
  AND scheduled_start < CURRENT_DATE + interval '7 days'
ORDER BY scheduled_start

-- Meetings with transcripts/summaries
SELECT title, scheduled_start, summary, action_items
FROM meetings
WHERE summary IS NOT NULL
ORDER BY scheduled_start DESC
LIMIT 10

-- Meetings with a specific person
SELECT title, scheduled_start, participants
FROM meetings
WHERE participants @> '[{"email": "john@example.com"}]'
```

### activities
Raw activity records (emails, calendar events, transcripts, messages).
Activities are linked to canonical entities via meeting_id, deal_id, account_id.

Query activities by TYPE, not source:
- `type = 'email'` for all emails (Gmail, Outlook, etc.)
- `type = 'meeting'` for calendar events
- `type = 'meeting_transcript'` for transcripts
- `type = 'slack_message'` for team messages

```
id (UUID, PK)
organization_id (UUID)
meeting_id (UUID, FK -> meetings, nullable) -- links to canonical meeting
deal_id, account_id, contact_id (UUID, nullable)
type (VARCHAR) -- 'email', 'meeting', 'meeting_transcript', 'slack_message', 'call', 'note'
subject (TEXT)
description (TEXT)
activity_date (TIMESTAMP)
custom_fields (JSONB)
```

## Data Types

### Emails (type = 'email')
All email communications, regardless of provider.
- subject, description (body preview), activity_date
- custom_fields: from_email, from_name, to_emails, cc_emails, has_attachments

```sql
SELECT subject, activity_date, custom_fields->>'from_email' as sender
FROM activities WHERE type = 'email'
ORDER BY activity_date DESC LIMIT 20
```

### Calendar Events (type = 'meeting')
Individual calendar entries - linked to canonical meetings via meeting_id.
- subject, activity_date, custom_fields: duration_minutes, attendee_emails, location, conference_link

### Meeting Transcripts (type = 'meeting_transcript')  
Transcripts and notes - linked to canonical meetings via meeting_id.
- subject, description (summary), activity_date
- custom_fields: duration_minutes, participants, keywords, has_action_items

### Messages (type = 'slack_message')
Team chat messages from Slack and similar tools.
- subject (channel name), description (message text), activity_date

## Guidelines

1. **Query meetings table for meeting info** - it's the canonical, deduplicated source.
2. **Query activities by type, not source_system** - use `type = 'email'` not `source_system = 'gmail'`.
3. **Use SQL for complex queries** - JOINs, aggregations, date filtering.
4. **JSONB queries**: Use -> for objects, ->> for text. E.g. `custom_fields->>'from_email'`
5. **Limit results**: Use LIMIT to avoid overwhelming responses.
6. **Explain your analysis**: Provide insights and recommendations, not just data.
7. **Distinguish internal vs external people**:
   - `users` = internal teammates (colleagues, sales reps, team members)
   - `contacts` = external people (customers, prospects, leads at other companies)

You have access to the user's CRM data, emails, calendar, meeting transcripts, and team messages - all normalized and deduplicated."""


class ChatOrchestrator:
    """Orchestrates chat interactions with Claude."""

    def __init__(
        self,
        user_id: str | None,
        organization_id: str | None,
        conversation_id: str | None = None,
        user_email: str | None = None,
        user_name: str | None = None,
        organization_name: str | None = None,
        local_time: str | None = None,
        timezone: str | None = None,
        source_user_id: str | None = None,
        source_user_email: str | None = None,
        workflow_context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize the orchestrator.

        Args:
            user_id: UUID of the authenticated user (None for Slack DM conversations)
            organization_id: UUID of the user's organization (may be None for new users)
            conversation_id: UUID of the conversation (may be None for new conversations)
            user_email: Email of the authenticated user
            user_name: Display name of the authenticated user
            organization_name: Name of the user's organization
            local_time: ISO timestamp of user's local time
            timezone: User's timezone (e.g., "America/New_York")
            source_user_id: External sender ID (e.g. Slack user ID)
            source_user_email: External sender email (e.g. Slack profile email)
            workflow_context: Optional workflow context for auto-approvals:
                - is_workflow: bool
                - workflow_id: str
                - auto_approve_tools: list[str]
                - auto_approve_permissions: list[str]
        """
        self.user_id = user_id
        self.organization_id = organization_id
        self.conversation_id = conversation_id
        self.user_email = user_email
        self.user_name = user_name
        self.organization_name = organization_name
        self.local_time = local_time
        self.timezone = timezone
        self.source_user_id = source_user_id
        self.source_user_email = source_user_email
        self.workflow_context = workflow_context
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        # Track if we've saved the assistant message (for early save during tool execution)
        self._assistant_message_saved = False

    async def _resolve_user_context(self) -> None:
        """Fetch user name and organization name from DB if not already set."""
        from models.user import User
        from models.organization import Organization

        try:
            async with get_session(organization_id=self.organization_id) as session:
                if not self.user_name and self.user_id:
                    result = await session.execute(
                        select(User.name).where(User.id == UUID(self.user_id))
                    )
                    name: str | None = result.scalar_one_or_none()
                    if name:
                        self.user_name = name

                if not self.organization_name and self.organization_id:
                    result = await session.execute(
                        select(Organization.name).where(
                            Organization.id == UUID(self.organization_id)
                        )
                    )
                    org_name: str | None = result.scalar_one_or_none()
                    if org_name:
                        self.organization_name = org_name
        except Exception:
            logger.warning("Failed to resolve user context", exc_info=True)

    async def _load_user_memories(self) -> list[dict[str, str]]:
        """Load all saved memories for the current user."""
        from models.user_memory import UserMemory

        try:
            async with get_session(organization_id=self.organization_id) as session:
                result = await session.execute(
                    select(UserMemory)
                    .where(UserMemory.user_id == UUID(self.user_id))  # type: ignore[arg-type]
                    .order_by(UserMemory.created_at.asc())
                )
                rows: list[UserMemory] = list(result.scalars().all())
                return [{"id": str(m.id), "content": m.content} for m in rows]
        except Exception:
            logger.warning("Failed to load user memories", exc_info=True)
            return []

    async def process_message(
        self,
        user_message: str,
        save_user_message: bool = True,
        skip_history: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process a user message and stream Claude's response with true streaming.

        Args:
            user_message: The user's message text
            save_user_message: If False, don't save user_message to DB (for internal system messages)
            skip_history: If True, skip loading history from DB (e.g. first message in a new conversation)
            attachment_ids: Optional list of upload IDs for attached files

        Yields:
            String chunks of the assistant's response (text streams immediately)
        """
        # Create conversation if needed
        if not self.conversation_id:
            self.conversation_id = await self._create_conversation()

        # Resolve attachment metadata before save (files are consumed by _build_user_content)
        attachment_meta: list[dict[str, Any]] = []
        if attachment_ids:
            from services.file_handler import retrieve_file, StoredFile
            for aid in attachment_ids:
                sf: StoredFile | None = retrieve_file(aid)
                if sf is not None:
                    attachment_meta.append({
                        "type": "attachment",
                        "filename": sf.filename,
                        "mimeType": sf.mime_type,
                        "size": sf.size,
                    })

        # Fire-and-forget user message save — it's for persistence, not the Claude call.
        if save_user_message:
            asyncio.create_task(self._save_user_message_safe(user_message, attachment_meta))

        # Skip history DB call for new conversations (zero messages to load).
        if skip_history:
            history: list[dict[str, Any]] = []
            logger.info("[Orchestrator] Skipped history load (new conversation)")
        else:
            history = await self._load_history(limit=20)
            logger.info("[Orchestrator] Loaded %d history messages", len(history))

        # Build user content — may include attachment blocks (images, PDFs, text)
        user_content: str | list[dict[str, Any]] = self._build_user_content(
            user_message, attachment_ids,
        )

        # Add user message to context for Claude
        messages: list[dict[str, Any]] = history + [
            {"role": "user", "content": user_content}
        ]

        # Keep track of content blocks for saving (preserves interleaving order)
        content_blocks: list[dict[str, Any]] = []

        # Build system prompt with user and time context
        system_prompt = SYSTEM_PROMPT

        # Resolve user_name and organization_name if not already set
        if self.user_id and (not self.user_name or not self.organization_name):
            await self._resolve_user_context()
        
        # Add user context so the agent knows who "me" is
        if self.user_email and self.user_id:
            user_context = f"\n\n## Current User\n"
            if self.user_name:
                user_context += f"- Name: {self.user_name}\n"
            user_context += f"- Email: {self.user_email}\n"
            user_context += f"- User ID: {self.user_id}\n"
            if self.organization_name:
                user_context += f"- Organization: {self.organization_name}\n"
            user_context += "\nWhen the user asks about 'my' data, use this email to filter queries. "
            user_context += "For example, to find the user's company, join the users table (filter by email) to the organizations table."
            system_prompt += user_context
        elif not self.user_id:
            # Slack conversation - no specific RevTops user context
            system_prompt += "\n\n## Current User\nThis conversation is from Slack. The specific user is not identified in Revtops."

        # Add Slack channel context so the agent can scope queries to the right channel
        slack_channel_id: str | None = (self.workflow_context or {}).get("slack_channel_id")
        if slack_channel_id:
            system_prompt += f"""

## Slack Channel Context
This conversation is happening in Slack channel ID: {slack_channel_id}

When the user asks about activity in "this channel", query the activities table filtered by:
```sql
WHERE source_system = 'slack' AND custom_fields->>'channel_id' = '{slack_channel_id}'
```
The activities table contains synced Slack messages with these relevant custom_fields keys: channel_id, channel_name, user_id, thread_ts."""
        
        if self.local_time or self.timezone:
            time_context = "\n\n## Current Time Context\n"
            if self.local_time:
                time_context += f"- User's local time: {self.local_time}\n"
            if self.timezone:
                time_context += f"- User's timezone: {self.timezone}\n"
            time_context += """
**IMPORTANT - Datetime Handling**:

1. **Storage**: All database timestamps are stored and returned in UTC.

2. **Format**: All datetime values in query results use ISO 8601 format with 'Z' suffix (e.g., "2026-02-04T18:00:00Z"). This 'Z' indicates UTC time.

3. **User Queries**: When the user asks about "today", "this morning", "yesterday", etc., convert their local date to UTC for queries:
   - Extract the user's local date from their local_time
   - Use that date in WHERE clauses, NOT CURRENT_DATE (which is UTC and may differ)
   - Example: If user's local time is 2026-01-27T20:00:00 in America/Los_Angeles, "today" means Jan 27 local time

4. **Query Example**:
```sql
-- Use explicit date literals based on user's local date
WHERE scheduled_start >= '2026-01-27'::date AND scheduled_start < '2026-01-28'::date
```

5. **Displaying Results**: Convert UTC times to the user's timezone when presenting results. Use relative references when helpful (e.g., "in 30 minutes", "3 hours ago")."""
            system_prompt += time_context

        # Load and inject user memories
        if self.user_id and self.organization_id:
            memories = await self._load_user_memories()
            if memories:
                memory_context = "\n\n## User Memories\n"
                memory_context += "These are preferences and facts the user previously asked you to remember. Follow them.\n\n"
                for mem in memories:
                    memory_context += f"- [{mem['id']}] {mem['content']}\n"
                memory_context += "\nIf the user asks you to forget something, use the delete_memory tool with the memory_id shown in brackets above."
                system_prompt += memory_context

        # Stream responses with tool handling loop
        async for chunk in self._stream_with_tools(messages, system_prompt, content_blocks):
            yield chunk
        
        # Save conversation (user message was already saved at the start)
        is_first_message = len(history) == 0
        
        # Debug: log content_blocks order
        logger.info("[Orchestrator] Saving content_blocks: %s", 
                    [(b.get("type"), b.get("name") if b.get("type") == "tool_use" else b.get("text", "")[:50]) 
                     for b in content_blocks])
        
        await self._save_assistant_message(content_blocks)

        # Update conversation title if first message
        if is_first_message:
            title = self._generate_title(user_message)
            await self._update_conversation_title(title)

    async def _stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        content_blocks: list[dict[str, Any]],
    ) -> AsyncGenerator[str, None]:
        """
        Stream Claude's response, handling tool calls in a loop.
        
        Uses true streaming - text is yielded immediately as tokens arrive.
        Tool calls are accumulated and executed when complete.
        Includes retry logic for transient API errors (overloaded, rate limits).
        """
        # Retry configuration
        max_retries = 3
        base_delay = 1.0  # seconds
        
        while True:
            # Track state for this streaming response
            current_text = ""
            tool_uses: list[dict[str, Any]] = []
            current_tool: dict[str, Any] | None = None
            current_tool_input_json = ""
            final_message = None
            
            # Retry loop for transient API errors
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    # Reset state on retry (in case partial data was received)
                    current_text = ""
                    tool_uses = []
                    current_tool = None
                    current_tool_input_json = ""
                    
                    # Stream the response
                    async with self.client.messages.stream(
                        model="claude-sonnet-4-20250514",
                        max_tokens=16384,
                        system=system_prompt,
                        tools=get_tools(),
                        messages=messages,
                    ) as stream:
                        async for event in stream:
                            # Handle different event types
                            if event.type == "content_block_start":
                                if event.content_block.type == "text":
                                    # Text block starting - nothing to do yet
                                    pass
                                elif event.content_block.type == "tool_use":
                                    # Tool use block starting - capture id and name
                                    current_tool = {
                                        "id": event.content_block.id,
                                        "name": event.content_block.name,
                                        "input": {},
                                    }
                                    current_tool_input_json = ""
                                    # Immediately notify frontend that a tool call is starting
                                    # so it can show a spinner while the input JSON streams
                                    yield json.dumps({
                                        "type": "tool_call_start",
                                        "tool_name": event.content_block.name,
                                        "tool_id": event.content_block.id,
                                    })
                            
                            elif event.type == "content_block_delta":
                                if event.delta.type == "text_delta":
                                    # Stream text immediately!
                                    text = event.delta.text
                                    current_text += text
                                    yield text
                                elif event.delta.type == "input_json_delta":
                                    # Accumulate tool input JSON
                                    current_tool_input_json += event.delta.partial_json
                            
                            elif event.type == "content_block_stop":
                                if current_tool is not None:
                                    # Parse the accumulated JSON for tool input
                                    try:
                                        current_tool["input"] = json.loads(current_tool_input_json) if current_tool_input_json else {}
                                    except json.JSONDecodeError:
                                        logger.warning("[Orchestrator] Failed to parse tool input JSON: %s", current_tool_input_json)
                                        current_tool["input"] = {}
                                    
                                    tool_uses.append(current_tool)
                                    current_tool = None
                                    current_tool_input_json = ""
                        
                        # Get the final message for conversation history
                        final_message = await stream.get_final_message()
                    
                    # Success - break out of retry loop
                    break
                    
                except APIStatusError as e:
                    last_error = e
                    error_type = getattr(e, "body", {}).get("error", {}).get("type", "") if isinstance(getattr(e, "body", None), dict) else ""
                    
                    # Check if this is a retryable error (includes 500 Internal Server Error)
                    is_retryable = error_type in ("overloaded_error", "rate_limit_error", "api_error") or e.status_code in (429, 500, 502, 503, 529)
                    
                    if is_retryable and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                        logger.warning(
                            "[Orchestrator] Retryable API error (attempt %d/%d): %s. Retrying in %.1fs...",
                            attempt + 1, max_retries, error_type or e.status_code, delay
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # Non-retryable error or max retries exceeded
                        logger.error("[Orchestrator] API error after %d attempts: %s", attempt + 1, e)
                        raise
            
            # If we exhausted retries without success, raise the last error
            if final_message is None and last_error is not None:
                raise last_error
            
            # If no tool calls, we're done
            if not tool_uses:
                # Save text to content_blocks
                if current_text.strip():
                    content_blocks.append({"type": "text", "text": current_text})
                break
            
            # Flush current text to content_blocks before processing tools
            if current_text.strip():
                content_blocks.append({"type": "text", "text": current_text})
            
            # Signal frontend to complete current text block before showing tools
            yield json.dumps({"type": "text_block_complete"})
            
            # === EARLY SAVE: Add tool_use blocks with "running" status and save message ===
            # This allows long-running tools to update their progress in the database
            tool_block_indices: dict[str, int] = {}  # tool_id -> index in content_blocks
            
            for tool_use in tool_uses:
                tool_id: str = tool_use["id"]
                tool_name: str = tool_use["name"]
                tool_input: dict[str, Any] = tool_use["input"]
                
                tool_block_indices[tool_id] = len(content_blocks)
                content_blocks.append({
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": tool_input,
                    "result": None,
                    "status": "running",
                })
                
                # Send tool call info as JSON for frontend to display
                yield json.dumps({
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_id": tool_id,
                    "status": "running",
                })
            
            # Early save: fire-and-forget so it doesn't block tool execution.
            # This persists the "running" tool_use blocks for reconnect catchup,
            # but the UI gets tool_call events via the yield above — no need to wait.
            if self.conversation_id:
                logger.info(
                    "[Orchestrator] Early save (background): %d blocks, _assistant_message_saved=%s",
                    len(content_blocks),
                    self._assistant_message_saved,
                )
                # Copy blocks snapshot for background save (list is mutated during tool execution)
                blocks_snapshot: list[dict[str, Any]] = [dict(b) for b in content_blocks]
                asyncio.create_task(self._save_assistant_message_safe(blocks_snapshot))
                self._assistant_message_saved = True
            
            # === EXECUTE TOOLS: Process each tool and update results ===
            tool_results: list[dict[str, Any]] = []
            
            for tool_use in tool_uses:
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                tool_id = tool_use["id"]

                logger.info(
                    "[Orchestrator] Tool call: %s | input=%s | org_id=%s | user_id=%s",
                    tool_name,
                    tool_input,
                    self.organization_id,
                    self.user_id,
                )

                # Build context with conversation_id and tool_id for progress updates
                tool_context: dict[str, Any] = {}
                if self.workflow_context:
                    tool_context.update(self.workflow_context)
                if self.conversation_id:
                    tool_context["conversation_id"] = self.conversation_id
                tool_context["tool_id"] = tool_id

                # Execute tool
                tool_result = await execute_tool(
                    tool_name, tool_input, self.organization_id, self.user_id,
                    context=tool_context,
                )

                logger.info(
                    "[Orchestrator] Tool result for %s: %s",
                    tool_name,
                    tool_result,
                )

                # Update the tool_use block in content_blocks with final result
                block_idx = tool_block_indices[tool_id]
                content_blocks[block_idx]["result"] = tool_result
                content_blocks[block_idx]["status"] = "complete"

                # Send tool result to frontend FIRST — don't block on DB write
                yield json.dumps({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "result": tool_result,
                    "status": "complete",
                })

                # If this was a create_artifact call, emit an artifact block
                if tool_name == "create_artifact" and tool_result.get("status") == "success":
                    artifact_data: dict[str, Any] | None = tool_result.get("artifact")
                    if artifact_data:
                        # Emit artifact block for frontend to render
                        yield json.dumps({
                            "type": "artifact",
                            "artifact": artifact_data,
                        })
                        # Also add artifact block to content_blocks for persistence
                        content_blocks.append({
                            "type": "artifact",
                            "artifact": artifact_data,
                        })

                # Persist tool result to DB in background (fire-and-forget).
                # The final _save_assistant_message at the end is the authoritative save.
                if self.conversation_id:
                    asyncio.create_task(self._update_tool_result_safe(
                        self.conversation_id, tool_id, tool_result, self.organization_id,
                    ))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": str(tool_result),
                })
            
            # Add assistant message with all tool uses, then user message with all results
            # Convert content blocks to plain dicts to avoid Pydantic serialization issues
            assistant_content: list[dict[str, Any]] = []
            for block in final_message.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

    @staticmethod
    def _build_user_content(
        user_message: str,
        attachment_ids: list[str] | None,
    ) -> str | list[dict[str, Any]]:
        """
        Build the ``content`` value for a Claude user message.

        If there are no attachments, returns a plain string (most common path).
        If there are attachments, returns a list of content blocks (images,
        documents, text) followed by the user's text message.
        """
        if not attachment_ids:
            return user_message

        from services.file_handler import (
            retrieve_file,
            remove_file,
            build_claude_content_blocks,
            StoredFile,
        )

        stored_files: list[StoredFile] = []
        for aid in attachment_ids:
            sf: StoredFile | None = retrieve_file(aid)
            if sf is not None:
                stored_files.append(sf)
            else:
                logger.warning("[Orchestrator] Attachment %s not found (expired?)", aid)

        if not stored_files:
            return user_message

        blocks: list[dict[str, Any]] = build_claude_content_blocks(stored_files)

        # Append the user's text as the final block
        blocks.append({"type": "text", "text": user_message})

        # Clean up temp storage now that we've consumed the files
        for sf in stored_files:
            remove_file(sf.upload_id)

        logger.info(
            "[Orchestrator] Built %d content block(s) from %d attachment(s)",
            len(blocks), len(stored_files),
        )
        return blocks

    async def _save_user_message_safe(
        self,
        user_msg: str,
        attachment_meta: list[dict[str, Any]] | None = None,
    ) -> None:
        """Fire-and-forget wrapper for _save_user_message. Logs errors instead of raising."""
        try:
            await self._save_user_message(user_msg, attachment_meta)
        except Exception as e:
            logger.warning("[Orchestrator] Background user message save failed: %s", e)

    async def _save_assistant_message_safe(self, blocks: list[dict[str, Any]]) -> None:
        """Fire-and-forget wrapper for _save_assistant_message. Logs errors instead of raising."""
        try:
            await self._save_assistant_message(blocks)
        except Exception as e:
            logger.warning("[Orchestrator] Background early save failed: %s", e)

    async def _update_tool_result_safe(
        self, conversation_id: str, tool_id: str, result: dict[str, Any], org_id: str | None,
    ) -> None:
        """Fire-and-forget wrapper for update_tool_result. Logs errors instead of raising."""
        try:
            await update_tool_result(conversation_id, tool_id, result, "complete", org_id)
        except Exception as e:
            logger.warning("[Orchestrator] Background tool result save failed: %s", e)

    async def _create_conversation(self) -> str:
        """Create a new conversation and return its ID."""
        user_uuid = UUID(self.user_id) if self.user_id else None
        async with get_session(organization_id=self.organization_id) as session:
            conversation = Conversation(
                user_id=user_uuid,
                organization_id=UUID(self.organization_id) if self.organization_id else None,
                title=None,
            )
            session.add(conversation)
            # Capture ID before commit (UUID is generated on model instantiation)
            conv_id = str(conversation.id)
            await session.commit()
            return conv_id

    async def _load_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Load recent chat history from the current conversation.
        
        Reconstructs proper Claude message format:
        - User messages with text content
        - Assistant messages with text + tool_use blocks
        - User messages with tool_result blocks (after tool_use)
        """
        if not self.conversation_id:
            return []

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == UUID(self.conversation_id))
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            messages = result.scalars().all()

            history: list[dict[str, Any]] = []
            for msg in reversed(messages):
                # Get content blocks (new format or convert from legacy)
                blocks = msg.content_blocks if msg.content_blocks else msg._legacy_to_blocks()
                
                if msg.role == "user":
                    # User messages: extract text content
                    text_content = ""
                    for block in blocks:
                        if block.get("type") == "text":
                            text_content += block.get("text", "")
                    if text_content:
                        history.append({"role": "user", "content": text_content})
                
                elif msg.role == "assistant":
                    # Check if there are tool_use blocks
                    tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
                    
                    if tool_uses:
                        # Need to reconstruct the conversation properly:
                        # 1. assistant: [pre-tool text + tool_use]
                        # 2. user: [tool_result]
                        # 3. assistant: [post-tool text] (if any)
                        
                        # Collect blocks before and after tool use
                        pre_tool_text: list[str] = []
                        post_tool_text: list[str] = []
                        current_tool_uses: list[dict[str, Any]] = []
                        tool_results: list[dict[str, Any]] = []
                        seen_tool = False
                        
                        for block in blocks:
                            if block.get("type") == "text":
                                text = block.get("text", "").strip()
                                if text:
                                    if not seen_tool:
                                        pre_tool_text.append(text)
                                    else:
                                        post_tool_text.append(text)
                            elif block.get("type") == "tool_use":
                                seen_tool = True
                                tool_id = block.get("id", f"tool_{len(current_tool_uses)}")
                                tool_name = block.get("name", "unknown")
                                tool_result = block.get("result")
                                
                                # Log what we're loading for debugging
                                logger.info(
                                    "[_load_history] Tool %s result: %s",
                                    tool_name,
                                    str(tool_result)[:200] if tool_result else "NO RESULT"
                                )
                                
                                current_tool_uses.append({
                                    "type": "tool_use",
                                    "id": tool_id,
                                    "name": tool_name,
                                    "input": block.get("input", {}),
                                })
                                
                                # Only add tool_result if we have actual result data
                                if tool_result is not None:
                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": tool_id,
                                        "content": json.dumps(tool_result) if isinstance(tool_result, dict) else str(tool_result),
                                    })
                                else:
                                    # If no result, indicate the tool was called but result is missing
                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": tool_id,
                                        "content": json.dumps({"error": "Result not available - tool execution may have failed"}),
                                    })
                        
                        # Build assistant message with pre-tool text + tool_use
                        claude_blocks: list[dict[str, Any]] = []
                        for text in pre_tool_text:
                            claude_blocks.append({"type": "text", "text": text})
                        claude_blocks.extend(current_tool_uses)
                        
                        if claude_blocks:
                            history.append({"role": "assistant", "content": claude_blocks})
                        
                        # Add tool_result as user message
                        if tool_results:
                            history.append({"role": "user", "content": tool_results})
                        
                        # Add post-tool text as assistant continuation
                        # Must have an assistant message after tool_result to avoid consecutive user messages
                        if post_tool_text:
                            history.append({"role": "assistant", "content": " ".join(post_tool_text)})
                        else:
                            # Build a summary of tool results to help Claude understand context
                            result_summaries: list[str] = []
                            for tr in tool_results:
                                try:
                                    content = json.loads(tr.get("content", "{}"))
                                    if "rows" in content:
                                        result_summaries.append(f"{content.get('row_count', len(content['rows']))} rows returned")
                                    elif "error" in content:
                                        result_summaries.append(f"error: {content['error'][:50]}")
                                except:
                                    pass
                            summary = ", ".join(result_summaries) if result_summaries else "results processed"
                            history.append({"role": "assistant", "content": f"Tool results: {summary}. I'll analyze these results."})
                    else:
                        # Simple text response - extract text from blocks
                        text_content = ""
                        for block in blocks:
                            if block.get("type") == "text":
                                text_content += block.get("text", "")
                        if text_content:
                            history.append({"role": "assistant", "content": text_content})
            
            return history

    async def _save_user_message(
        self,
        user_msg: str,
        attachment_meta: list[dict[str, Any]] | None = None,
    ) -> None:
        """Save user message to database immediately."""
        conv_uuid = UUID(self.conversation_id) if self.conversation_id else None
        user_uuid = UUID(self.user_id) if self.user_id else None

        # Build content blocks: attachment metadata first, then text
        blocks: list[dict[str, Any]] = []
        if attachment_meta:
            blocks.extend(attachment_meta)
        blocks.append({"type": "text", "text": user_msg})

        async with get_session(organization_id=self.organization_id) as session:
            session.add(
                ChatMessage(
                    conversation_id=conv_uuid,
                    user_id=user_uuid,
                    organization_id=UUID(self.organization_id) if self.organization_id else None,
                    role="user",
                    content_blocks=blocks,
                    source_user_id=self.source_user_id,
                    source_user_email=self.source_user_email,

                )
            )

            # Update conversation's cached fields
            if conv_uuid:
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == conv_uuid)
                    .values(
                        updated_at=datetime.utcnow(),
                        message_count=Conversation.message_count + 1,
                        last_message_preview=user_msg[:200] if user_msg else None,
                    )
                )

            await session.commit()
            logger.info("[Orchestrator] Saved user message to conversation %s", self.conversation_id)

    async def _save_assistant_message(self, assistant_blocks: list[dict[str, Any]]) -> None:
        """Save or update assistant message in database."""
        conv_uuid = UUID(self.conversation_id) if self.conversation_id else None
        user_uuid = UUID(self.user_id) if self.user_id else None
        logger.info(
            "[Orchestrator] _save_assistant_message: _saved=%s, conv=%s",
            self._assistant_message_saved,
            conv_uuid,
        )

        async with get_session(organization_id=self.organization_id) as session:
            if self._assistant_message_saved:
                # UPDATE existing message (we saved early during tool execution)
                # Find the latest assistant message and update its content
                query = (
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conv_uuid)
                    .where(ChatMessage.role == "assistant")
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
                result = await session.execute(query)
                message = result.scalar_one_or_none()
                
                if message:
                    logger.info("[Orchestrator] UPDATE existing assistant message %s", message.id)
                    message.content_blocks = assistant_blocks
                else:
                    # Fallback to insert if not found
                    session.add(
                        ChatMessage(
                            conversation_id=conv_uuid,
                            user_id=user_uuid,
                            organization_id=UUID(self.organization_id) if self.organization_id else None,
                            role="assistant",
                            content_blocks=assistant_blocks,
                        )
                    )
            else:
                # INSERT new message
                logger.info("[Orchestrator] INSERT new assistant message")
                session.add(
                    ChatMessage(
                        conversation_id=conv_uuid,
                        user_id=user_uuid,
                        organization_id=UUID(self.organization_id) if self.organization_id else None,
                        role="assistant",
                        content_blocks=assistant_blocks,
                    )
                )

            # Update conversation's cached fields
            if conv_uuid:
                # Extract text preview from content blocks
                preview_text: str | None = None
                for block in assistant_blocks:
                    if block.get("type") == "text" and block.get("text"):
                        preview_text = block["text"][:200]
                        break

                # Only increment message_count if this is a new message
                if self._assistant_message_saved:
                    await session.execute(
                        update(Conversation)
                        .where(Conversation.id == conv_uuid)
                        .values(
                            updated_at=datetime.utcnow(),
                            last_message_preview=preview_text,
                        )
                    )
                else:
                    await session.execute(
                        update(Conversation)
                        .where(Conversation.id == conv_uuid)
                        .values(
                            updated_at=datetime.utcnow(),
                            message_count=Conversation.message_count + 1,
                            last_message_preview=preview_text,
                        )
                    )

            await session.commit()

    async def _update_conversation_title(self, title: str) -> None:
        """Update the conversation title."""
        if not self.conversation_id:
            return

        async with get_session(organization_id=self.organization_id) as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == UUID(self.conversation_id))
                .values(title=title, updated_at=datetime.utcnow())
            )
            await session.commit()

    def _generate_title(self, message: str) -> str:
        """Generate a title from the first message."""
        # Clean and truncate the message
        cleaned = message.strip().replace("\n", " ")

        # If it's a question, use it as-is (truncated)
        if cleaned.endswith("?") and len(cleaned) <= 50:
            return cleaned

        # Otherwise, create a summary
        words = cleaned.split(" ")[:6]
        title = " ".join(words)

        if len(title) > 40:
            title = title[:40]

        # Add ellipsis if truncated
        if len(cleaned) > len(title):
            title += "..."

        return title or "New Chat"
