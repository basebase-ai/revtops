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

import json
import logging
from datetime import datetime
from typing import Any, AsyncGenerator
from uuid import UUID

from anthropic import AsyncAnthropic
from sqlalchemy import select, update

from agents.tools import execute_tool, get_tools
from config import settings
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_session

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Revtops, an AI assistant for sales and revenue operations.

You help users understand their sales pipeline, analyze deals, and get insights from their CRM data.

## Communication Style

**IMPORTANT: Always explain what you're doing before using tools.** When you need to call a tool, first write a brief message explaining your approach. For example:
- "Let me check your recent deal activity..." (before running a SQL query)
- "I'll search for emails related to that topic..." (before semantic search)
- "Let me look that up for you..." (before web search)

This helps users understand what you're thinking and what to expect.

## Available Tools

You have access to powerful tools:
- **run_sql_query**: Execute arbitrary SELECT queries against the database. Use this for structured data analysis, exact text matching, and complex joins.
- **search_activities**: Semantic search across emails, meetings, and messages. Use this when users want to find activities by meaning/concept rather than exact text (e.g., "find emails about pricing discussions").
- **create_artifact**: Save dashboards, reports, or analyses for the user.
- **web_search**: Search the web for external information not in the user's data. Use this for industry benchmarks, company research, market trends, news, and sales methodologies.
- **crm_write**: Create or update records in the CRM (HubSpot). This shows a preview and requires user approval before executing.
- **create_workflow**: Create automated workflows that run on schedules or events.
- **trigger_workflow**: Manually run a workflow to test it.

### When to use which tool:
- **search_activities**: For conceptual/semantic queries like "emails about contract renewal", "meetings discussing budget"
- **run_sql_query with ILIKE**: For exact patterns like "emails from @acmecorp.com", "meetings with John Smith"
- **web_search**: For external context like "typical enterprise SaaS close rates", "what does Acme Corp do", "MEDDIC qualification framework"
- **crm_write**: When the user wants to create contacts, companies, or deals in their CRM from prospect lists or other data

### CRM Write Operations

When users want to create or update CRM records, use the **crm_write** tool. This tool:
1. Validates the input data
2. Checks for duplicates in the CRM
3. Shows the user a preview with Approve/Cancel buttons
4. Only executes after user approval

**IMPORTANT: Always explain what you're going to create BEFORE calling the crm_write tool.**

Follow this sequence:
1. First, write a brief message explaining what records you'll create (e.g., "I'll create a contact for John Smith and a company for Acme Corp in HubSpot.")
2. Then call the crm_write tool(s)
3. The tool will show the user an approval card - they'll click Approve or Cancel

Example usage:
- User provides a list of prospects → explain what you'll create → then call crm_write
- User wants to create a company → explain → then call crm_write with company record_type
- User wants to create deals → explain → then call crm_write with deal record_type

Property names for each record type:
- **contact**: email (required), firstname, lastname, company, jobtitle, phone
- **company**: name (required), domain, industry, numberofemployees
- **deal**: dealname (required), amount, dealstage, closedate, pipeline

The tool returns a "pending_approval" status. Do NOT add any text after the tool call - just let the approval card speak for itself.

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
People associated with accounts.
```
id, organization_id, account_id, name, email, title, phone, custom_fields
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

You have access to the user's CRM data, emails, calendar, meeting transcripts, and team messages - all normalized and deduplicated."""


class ChatOrchestrator:
    """Orchestrates chat interactions with Claude."""

    def __init__(
        self,
        user_id: str,
        organization_id: str | None,
        conversation_id: str | None = None,
        user_email: str | None = None,
        local_time: str | None = None,
        timezone: str | None = None,
    ) -> None:
        """
        Initialize the orchestrator.

        Args:
            user_id: UUID of the authenticated user
            organization_id: UUID of the user's organization (may be None for new users)
            conversation_id: UUID of the conversation (may be None for new conversations)
            user_email: Email of the authenticated user
            local_time: ISO timestamp of user's local time
            timezone: User's timezone (e.g., "America/New_York")
        """
        self.user_id = user_id
        self.organization_id = organization_id
        self.conversation_id = conversation_id
        self.user_email = user_email
        self.local_time = local_time
        self.timezone = timezone
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def process_message(
        self, user_message: str, save_user_message: bool = True
    ) -> AsyncGenerator[str, None]:
        """
        Process a user message and stream Claude's response.

        Flow:
        1. Create conversation if needed
        2. Load conversation history
        3. Add user message
        4. Call Claude with tools
        5. Handle tool calls if any
        6. Stream response
        7. Save to database
        8. Update conversation title if first message

        Args:
            user_message: The user's message text
            save_user_message: If False, don't save user_message to DB (for internal system messages)

        Yields:
            String chunks of the assistant's response
        """
        # Create conversation if needed
        if not self.conversation_id:
            self.conversation_id = await self._create_conversation()

        # Save user message immediately (so it's visible even if response fails/is interrupted)
        if save_user_message:
            await self._save_user_message(user_message)

        # Load conversation history (only from this conversation)
        history = await self._load_history(limit=20)

        # Add user message to context for Claude
        messages: list[dict[str, Any]] = history + [
            {"role": "user", "content": user_message}
        ]

        # Keep track of content blocks for saving (preserves interleaving order)
        content_blocks: list[dict[str, Any]] = []
        current_text = ""  # Buffer for current text block

        # Build system prompt with user and time context
        system_prompt = SYSTEM_PROMPT
        
        # Add user context so the agent knows who "me" is
        if self.user_email:
            user_context = f"\n\n## Current User\n"
            user_context += f"- Email: {self.user_email}\n"
            user_context += f"- User ID: {self.user_id}\n"
            user_context += "\nWhen the user asks about 'my' data, use this email to filter queries. "
            user_context += "For example, to find the user's company, join the users table (filter by email) to the organizations table."
            system_prompt += user_context
        
        if self.local_time or self.timezone:
            time_context = "\n\n## Current Time Context\n"
            if self.local_time:
                time_context += f"- User's local time: {self.local_time}\n"
            if self.timezone:
                time_context += f"- User's timezone: {self.timezone}\n"
            time_context += """
**IMPORTANT**: All database timestamps are stored in UTC. When the user asks about "today", "this morning", "yesterday", etc., you must convert their local date to UTC for accurate queries.

For date-based queries, use the user's timezone to calculate the correct UTC range:
- Extract the user's local date from their local_time
- Use that date in your WHERE clauses, NOT CURRENT_DATE (which is UTC and may differ)
- Example: If user's local time is 2026-01-27T20:00:00 in America/Los_Angeles, "today" means Jan 27 local time, even though CURRENT_DATE in UTC might be Jan 28

When querying for "today" or "this morning", use explicit date literals based on the user's local date:
```sql
-- Instead of: WHERE scheduled_start >= CURRENT_DATE
-- Use: WHERE scheduled_start >= '2026-01-27'::date AND scheduled_start < '2026-01-28'::date
```

Use the user's local time to provide relative references (e.g., '3 hours ago', 'yesterday') when discussing results."""
            system_prompt += time_context

        # Initial Claude call (async)
        response = await self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=system_prompt,
            tools=get_tools(),
            messages=messages,
        )

        # Process response - handle tool calls in a loop until no more tool use
        while True:
            # Extract text and tool_use blocks from response
            tool_uses: list[Any] = []
            
            for content_block in response.content:
                if content_block.type == "text":
                    text = content_block.text
                    current_text += text
                    yield text
                elif content_block.type == "tool_use":
                    tool_uses.append(content_block)
            
            # If no tool calls, we're done
            if not tool_uses:
                break
            
            # Flush current text to content_blocks before processing tools
            if current_text.strip():
                content_blocks.append({"type": "text", "text": current_text})
                current_text = ""
            
            # Signal frontend to complete current text block before showing tools
            yield json.dumps({"type": "text_block_complete"})
            
            # Process ALL tool calls from this response
            tool_results: list[dict[str, Any]] = []
            
            for tool_use in tool_uses:
                tool_name = tool_use.name
                tool_input = tool_use.input
                tool_id = tool_use.id

                logger.info(
                    "[Orchestrator] Tool call: %s | input=%s | org_id=%s | user_id=%s",
                    tool_name,
                    tool_input,
                    self.organization_id,
                    self.user_id,
                )

                # Send tool call info as JSON for frontend to display
                yield json.dumps({
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_id": tool_id,
                    "status": "running",
                })

                # Execute tool
                tool_result = await execute_tool(
                    tool_name, tool_input, self.organization_id, self.user_id
                )

                logger.info(
                    "[Orchestrator] Tool result for %s: %s",
                    tool_name,
                    tool_result,
                )

                # Add tool_use block with result to content_blocks
                content_blocks.append({
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": tool_input,
                    "result": tool_result,
                    "status": "complete",
                })

                # Send tool result for frontend
                yield json.dumps({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "result": tool_result,
                    "status": "complete",
                })

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": str(tool_result),
                })
            
            # Add assistant message with all tool uses, then user message with all results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            # Get Claude's response to all tool results (async)
            response = await self.client.messages.create(
                model="claude-opus-4-5",
                max_tokens=4096,
                system=system_prompt,
                tools=get_tools(),
                messages=messages,
            )

        # Flush any remaining text to content_blocks
        if current_text.strip():
            content_blocks.append({"type": "text", "text": current_text})
        
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

    async def _create_conversation(self) -> str:
        """Create a new conversation and return its ID."""
        async with get_session(organization_id=self.organization_id) as session:
            conversation = Conversation(
                user_id=UUID(self.user_id),
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
                                current_tool_uses.append({
                                    "type": "tool_use",
                                    "id": tool_id,
                                    "name": block.get("name", "unknown"),
                                    "input": block.get("input", {}),
                                })
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": json.dumps(block.get("result", {})),
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
                            # Minimal placeholder to prevent consecutive user messages
                            history.append({"role": "assistant", "content": "I've processed the tool results."})
                    else:
                        # Simple text response - extract text from blocks
                        text_content = ""
                        for block in blocks:
                            if block.get("type") == "text":
                                text_content += block.get("text", "")
                        if text_content:
                            history.append({"role": "assistant", "content": text_content})
            
            return history

    async def _save_user_message(self, user_msg: str) -> None:
        """Save user message to database immediately."""
        conv_uuid = UUID(self.conversation_id) if self.conversation_id else None

        async with get_session(organization_id=self.organization_id) as session:
            session.add(
                ChatMessage(
                    conversation_id=conv_uuid,
                    user_id=UUID(self.user_id),
                    organization_id=UUID(self.organization_id) if self.organization_id else None,
                    role="user",
                    content_blocks=[{"type": "text", "text": user_msg}],
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
        """Save assistant message to database."""
        conv_uuid = UUID(self.conversation_id) if self.conversation_id else None

        async with get_session(organization_id=self.organization_id) as session:
            session.add(
                ChatMessage(
                    conversation_id=conv_uuid,
                    user_id=UUID(self.user_id),
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
