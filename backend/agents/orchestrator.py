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
- **run_sql_query**: Execute arbitrary SELECT queries against the database. Use this for complex analysis.
- **create_artifact**: Save dashboards, reports, or analyses for the user.

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
CRM activities: calls, emails, meetings, notes.
```
id (UUID, PK)
organization_id (UUID, FK -> organizations)
source_system (VARCHAR)
source_id (VARCHAR, nullable)
deal_id (UUID, FK -> deals, nullable)
account_id (UUID, FK -> accounts, nullable)
contact_id (UUID, FK -> contacts, nullable)
type (VARCHAR, nullable) -- 'call', 'email', 'meeting', 'note'
subject (TEXT, nullable)
description (TEXT, nullable)
activity_date (TIMESTAMP, nullable)
created_by_id (UUID, FK -> users, nullable)
custom_fields (JSONB, nullable)
synced_at (TIMESTAMP)
```

### users
Users of the Revtops platform.
```
id (UUID, PK)
email (VARCHAR, unique)
name (VARCHAR, nullable)
organization_id (UUID, FK -> organizations, nullable)
salesforce_user_id (VARCHAR, nullable)
role (VARCHAR, nullable) -- 'ae', 'sales_manager', 'cro', 'admin'
created_at (TIMESTAMP)
last_login (TIMESTAMP, nullable)
```

### integrations
Connected integrations (HubSpot, Slack, etc.).
```
id (UUID, PK)
organization_id (UUID, FK -> organizations)
provider (VARCHAR) -- 'hubspot', 'slack', 'google_calendar', 'microsoft_calendar', 'salesforce'
nango_connection_id (VARCHAR, nullable)
connected_by_user_id (UUID, FK -> users, nullable)
is_active (BOOLEAN)
last_sync_at (TIMESTAMP, nullable)
last_error (TEXT, nullable)
extra_data (JSONB, nullable)
created_at (TIMESTAMP)
updated_at (TIMESTAMP)
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

Emails from Microsoft Mail (Outlook) are stored in the **activities** table with:
- `source_system = 'microsoft_mail'`
- `type = 'email'`
- `subject` = email subject
- `description` = email body preview
- `activity_date` = received timestamp
- `custom_fields` contains: from_email, from_name, to_emails, cc_emails, recipient_count, has_attachments, importance, is_read, conversation_id

Example query for recent emails:
```sql
SELECT subject, activity_date, 
       custom_fields->>'from_email' as from_email,
       custom_fields->>'from_name' as from_name,
       custom_fields->'to_emails' as to_emails
FROM activities 
WHERE source_system = 'microsoft_mail'
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
    ) -> None:
        """
        Initialize the orchestrator.

        Args:
            user_id: UUID of the authenticated user
            organization_id: UUID of the user's organization (may be None for new users)
            conversation_id: UUID of the conversation (may be None for new conversations)
        """
        self.user_id = user_id
        self.organization_id = organization_id
        self.conversation_id = conversation_id
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def process_message(
        self, user_message: str
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

        # Keep track of full response for saving
        assistant_message = ""
        tool_calls_made: list[dict[str, Any]] = []

        # Initial Claude call
        response = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
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
                    assistant_message += text
                    yield text
                elif content_block.type == "tool_use":
                    tool_uses.append(content_block)
            
            # If no tool calls, we're done
            if not tool_uses:
                break
            
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

                tool_calls_made.append(
                    {"name": tool_name, "input": tool_input, "id": tool_id}
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
                system=SYSTEM_PROMPT,
                tools=get_tools(),
                messages=messages,
            )

        # Save conversation
        is_first_message = len(history) == 0
        await self._save_messages(
            user_message, assistant_message, tool_calls_made if tool_calls_made else None
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

    async def _load_history(self, limit: int = 20) -> list[dict[str, str]]:
        """Load recent chat history from the current conversation."""
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

            return [
                {"role": msg.role, "content": msg.content} for msg in reversed(messages)
            ]

    async def _save_messages(
        self,
        user_msg: str,
        assistant_msg: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Save conversation to database."""
        conv_uuid = UUID(self.conversation_id) if self.conversation_id else None

        async with get_session() as session:
            # Save user message
            session.add(
                ChatMessage(
                    conversation_id=conv_uuid,
                    user_id=UUID(self.user_id),
                    role="user",
                    content=user_msg,
                )
            )

            # Save assistant message
            session.add(
                ChatMessage(
                    conversation_id=conv_uuid,
                    user_id=UUID(self.user_id),
                    role="assistant",
                    content=assistant_msg,
                    tool_calls=tool_calls,
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
