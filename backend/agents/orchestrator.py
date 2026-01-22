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

SYSTEM_PROMPT = """You are Revtops, an AI assistant for sales and revenue operations.

You help users understand their sales pipeline, analyze deals, and get insights from their CRM data.

You have access to tools that let you:
- Query deals from the database with various filters
- Query accounts with filters
- Create and save analyses, reports, and dashboards

When answering questions:
1. Use the available tools to fetch relevant data
2. Provide clear, actionable insights
3. Be concise but thorough
4. If you create an artifact (dashboard, report, analysis), mention it to the user

You have access to the user's Hubspot, Slack, Google Calendar, Salesforce, and other enterprise revenue operations data that has been synced to the system."""


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
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=get_tools(),
            messages=messages,
        )

        # Process response content
        for content_block in response.content:
            if content_block.type == "text":
                text = content_block.text
                assistant_message += text
                yield text

            elif content_block.type == "tool_use":
                # Execute the tool
                tool_name = content_block.name
                tool_input = content_block.input
                tool_id = content_block.id

                tool_calls_made.append(
                    {"name": tool_name, "input": tool_input, "id": tool_id}
                )

                yield f"\n\n*Querying {tool_name}...*\n\n"

                # Execute tool
                tool_result = await execute_tool(
                    tool_name, tool_input, self.organization_id, self.user_id
                )

                # Continue conversation with tool result
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": str(tool_result),
                            }
                        ],
                    }
                )

                # Get Claude's response to tool result
                followup_response = self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=get_tools(),
                    messages=messages,
                )

                for followup_block in followup_response.content:
                    if followup_block.type == "text":
                        text = followup_block.text
                        assistant_message += text
                        yield text

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
