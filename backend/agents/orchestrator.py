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

import anthropic
from sqlalchemy import select, update

from agents.tools import execute_tool, get_tools
from config import settings
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_session

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Revtops, an AI assistant for sales and revenue operations.

You help users understand their sales pipeline, analyze deals, and get insights from their CRM data.

## Available Tools

You have access to powerful tools:
- **run_sql_query**: Execute arbitrary SELECT queries against the database. Use this for structured data analysis, exact text matching, and complex joins.
- **search_activities**: Semantic search across emails, meetings, and messages. Use this when users want to find activities by meaning/concept rather than exact text (e.g., "find emails about pricing discussions").
- **create_artifact**: Save dashboards, reports, or analyses for the user.
- **web_search**: Search the web for external information not in the user's data. Use this for industry benchmarks, company research, market trends, news, and sales methodologies.
- **crm_write**: Create or update records in the CRM (HubSpot). This shows a preview and requires user approval before executing.

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

## Database Schema

All tables have `organization_id` for multi-tenancy. Your queries are automatically filtered to the user's organization.

### deals
Sales opportunities/deals from CRM.
```
id (UUID, PK)
organization_id (UUID, FK -> organizations)
source_system (VARCHAR) -- 'hubspot', 'salesforce', etc.
source_id (VARCHAR) -- ID in source system
name (VARCHAR) -- deal name
account_id (UUID, FK -> accounts, nullable)
owner_id (UUID, FK -> users, nullable) -- sales rep
amount (NUMERIC(15,2), nullable) -- deal value
stage (VARCHAR, nullable) -- e.g. 'appointmentscheduled', 'qualifiedtobuy', 'closedwon'
probability (INTEGER, nullable) -- win probability 0-100
close_date (DATE, nullable)
created_date (TIMESTAMP)
last_modified_date (TIMESTAMP)
visible_to_user_ids (UUID[], nullable) -- permission control
custom_fields (JSONB, nullable) -- e.g. {"pipeline": "default"}
synced_at (TIMESTAMP)
```

### accounts
Companies/organizations in the CRM.
```
id (UUID, PK)
organization_id (UUID, FK -> organizations)
source_system (VARCHAR)
source_id (VARCHAR)
name (VARCHAR)
domain (VARCHAR, nullable) -- company website domain
industry (VARCHAR, nullable)
employee_count (INTEGER, nullable)
annual_revenue (NUMERIC(15,2), nullable)
owner_id (UUID, FK -> users, nullable)
custom_fields (JSONB, nullable)
synced_at (TIMESTAMP)
```

### contacts
People associated with accounts.
```
id (UUID, PK)
organization_id (UUID, FK -> organizations)
source_system (VARCHAR)
source_id (VARCHAR)
account_id (UUID, FK -> accounts, nullable)
name (VARCHAR, nullable)
email (VARCHAR, nullable)
title (VARCHAR, nullable) -- job title
phone (VARCHAR, nullable)
custom_fields (JSONB, nullable)
synced_at (TIMESTAMP)
```

### activities
CRM activities: calls, emails, meetings, notes, calendar events.
```
id (UUID, PK)
organization_id (UUID, FK -> organizations)
source_system (VARCHAR) -- 'gmail', 'microsoft_mail', 'google_calendar', 'microsoft_calendar', 'slack', 'hubspot', 'salesforce'
source_id (VARCHAR, nullable)
deal_id (UUID, FK -> deals, nullable)
account_id (UUID, FK -> accounts, nullable)
contact_id (UUID, FK -> contacts, nullable)
type (VARCHAR, nullable) -- 'call', 'email', 'meeting', 'note', 'teams_meeting', 'google_meet'
subject (TEXT, nullable)
description (TEXT, nullable)
activity_date (TIMESTAMP, nullable)
created_by_id (UUID, FK -> users, nullable)
custom_fields (JSONB, nullable) -- varies by source, includes from_email, to_emails, attendees, etc.
searchable_text (TEXT, nullable) -- combined text used for semantic search
synced_at (TIMESTAMP)
```


## Calendar Data

Calendar events from Google Calendar and Microsoft Calendar (Outlook) are stored in the **activities** table with:
- `source_system = 'google_calendar'` or `source_system = 'microsoft_calendar'`
- `type` = 'meeting', 'google_meet', 'teams_meeting', 'zoom', or 'online_meeting'
- `subject` = meeting title
- `activity_date` = meeting start time
- `custom_fields` contains: duration_minutes, attendee_count, attendee_emails, conference_link, is_recurring, location

Example query for upcoming meetings:
```sql
SELECT subject, activity_date, type, custom_fields->>'duration_minutes' as duration
FROM activities 
WHERE source_system IN ('google_calendar', 'microsoft_calendar')
  AND activity_date >= CURRENT_TIMESTAMP
ORDER BY activity_date
LIMIT 10
```

## Email Data

Emails from Gmail and Microsoft Mail (Outlook) are stored in the **activities** table with:
- `source_system = 'gmail'` or `source_system = 'microsoft_mail'`
- `type = 'email'`
- `subject` = email subject
- `description` = email body preview/snippet
- `activity_date` = received timestamp
- `custom_fields` contains: from_email, from_name, to_emails, cc_emails, recipient_count, has_attachments

Gmail-specific custom_fields: is_unread, is_sent, labels, thread_id
Microsoft Mail custom_fields: importance, is_read, conversation_id

Example query for recent emails:
```sql
SELECT subject, activity_date, source_system,
       custom_fields->>'from_email' as from_email,
       custom_fields->>'from_name' as from_name,
       custom_fields->'to_emails' as to_emails
FROM activities 
WHERE source_system IN ('gmail', 'microsoft_mail')
ORDER BY activity_date DESC
LIMIT 20
```

## Guidelines

1. **Use SQL for complex queries**: The run_sql_query tool is powerful - use it for JOINs, aggregations, date filtering, etc.
2. **Be precise with dates**: Use PostgreSQL date functions. Current date: use CURRENT_DATE.
3. **Handle NULLs**: Many fields are nullable. Use COALESCE or IS NOT NULL as needed.
4. **JSONB queries**: Use -> for objects, ->> for text. E.g. `custom_fields->>'pipeline'`
5. **Limit results**: For large queries, use LIMIT to avoid overwhelming responses.
6. **Explain your analysis**: Don't just show data - provide insights and recommendations.

You have access to the user's HubSpot, Slack, Google Calendar, Salesforce, and other enterprise revenue operations data that has been synced to the system."""


class ChatOrchestrator:
    """Orchestrates chat interactions with Claude."""

    def __init__(
        self,
        user_id: str,
        organization_id: str | None,
        conversation_id: str | None = None,
        local_time: str | None = None,
        timezone: str | None = None,
    ) -> None:
        """
        Initialize the orchestrator.

        Args:
            user_id: UUID of the authenticated user
            organization_id: UUID of the user's organization (may be None for new users)
            conversation_id: UUID of the conversation (may be None for new conversations)
            local_time: ISO timestamp of user's local time
            timezone: User's timezone (e.g., "America/New_York")
        """
        self.user_id = user_id
        self.organization_id = organization_id
        self.conversation_id = conversation_id
        self.local_time = local_time
        self.timezone = timezone
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

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

        # Load conversation history (only from this conversation)
        history = await self._load_history(limit=20)

        # Add user message
        messages: list[dict[str, Any]] = history + [
            {"role": "user", "content": user_message}
        ]

        # Keep track of content blocks for saving (preserves interleaving order)
        content_blocks: list[dict[str, Any]] = []
        current_text = ""  # Buffer for current text block

        # Build system prompt with time context
        system_prompt = SYSTEM_PROMPT
        if self.local_time or self.timezone:
            time_context = "\n\n## Current Time Context\n"
            if self.local_time:
                time_context += f"- User's local time: {self.local_time}\n"
            if self.timezone:
                time_context += f"- User's timezone: {self.timezone}\n"
            time_context += "\nUse this to provide relative time references (e.g., '3 hours ago', 'yesterday') when discussing sync times, activity dates, etc."
            system_prompt = SYSTEM_PROMPT + time_context

        # Initial Claude call
        response = self.client.messages.create(
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

            # Get Claude's response to all tool results
            response = self.client.messages.create(
                model="claude-opus-4-5",
                max_tokens=4096,
                system=system_prompt,
                tools=get_tools(),
                messages=messages,
            )

        # Flush any remaining text to content_blocks
        if current_text.strip():
            content_blocks.append({"type": "text", "text": current_text})
        
        # Save conversation
        is_first_message = len(history) == 0
        await self._save_messages(
            user_message if save_user_message else None,
            content_blocks
        )

        # Update conversation title if first message
        if is_first_message:
            title = self._generate_title(user_message)
            await self._update_conversation_title(title)

    async def _create_conversation(self) -> str:
        """Create a new conversation and return its ID."""
        async with get_session() as session:
            conversation = Conversation(
                user_id=UUID(self.user_id),
                title=None,
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return str(conversation.id)

    async def _load_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Load recent chat history from the current conversation.
        
        Reconstructs proper Claude message format:
        - User messages with text content
        - Assistant messages with text + tool_use blocks
        - User messages with tool_result blocks (after tool_use)
        """
        if not self.conversation_id:
            return []

        async with get_session() as session:
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
                        # Build Claude-format content blocks: text + tool_use (no results)
                        claude_blocks: list[dict[str, Any]] = []
                        for block in blocks:
                            if block.get("type") == "text":
                                claude_blocks.append({"type": "text", "text": block.get("text", "")})
                            elif block.get("type") == "tool_use":
                                claude_blocks.append({
                                    "type": "tool_use",
                                    "id": block.get("id", f"tool_{len(claude_blocks)}"),
                                    "name": block.get("name", "unknown"),
                                    "input": block.get("input", {}),
                                })
                        
                        history.append({"role": "assistant", "content": claude_blocks})
                        
                        # Add corresponding tool_result in a user message
                        tool_results: list[dict[str, Any]] = []
                        for block in blocks:
                            if block.get("type") == "tool_use":
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.get("id", f"tool_{len(tool_results)}"),
                                    "content": json.dumps(block.get("result", {})),
                                })
                        if tool_results:
                            history.append({"role": "user", "content": tool_results})
                    else:
                        # Simple text response - extract text from blocks
                        text_content = ""
                        for block in blocks:
                            if block.get("type") == "text":
                                text_content += block.get("text", "")
                        if text_content:
                            history.append({"role": "assistant", "content": text_content})
            
            return history

    async def _save_messages(
        self,
        user_msg: str | None,
        assistant_blocks: list[dict[str, Any]],
    ) -> None:
        """Save conversation to database using content_blocks format."""
        conv_uuid = UUID(self.conversation_id) if self.conversation_id else None

        async with get_session() as session:
            # Save user message (skip if None - for internal system messages)
            if user_msg is not None:
                session.add(
                    ChatMessage(
                        conversation_id=conv_uuid,
                        user_id=UUID(self.user_id),
                        role="user",
                        content_blocks=[{"type": "text", "text": user_msg}],
                    )
                )

            # Save assistant message with content blocks
            session.add(
                ChatMessage(
                    conversation_id=conv_uuid,
                    user_id=UUID(self.user_id),
                    role="assistant",
                    content_blocks=assistant_blocks,
                )
            )

            # Update conversation's updated_at
            if conv_uuid:
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == conv_uuid)
                    .values(updated_at=datetime.utcnow())
                )

            await session.commit()

    async def _update_conversation_title(self, title: str) -> None:
        """Update the conversation title."""
        if not self.conversation_id:
            return

        async with get_session() as session:
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
