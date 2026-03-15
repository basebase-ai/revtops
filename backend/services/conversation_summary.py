"""
Async AI-generated conversation summaries.

Generates two-section summaries ("Overall" and "Recent Updates") using
Claude Haiku. Summaries are stored as JSON in the existing Conversation.summary
Text column and delivered to clients via WebSocket broadcast.

This is a non-critical background task — all errors are caught and logged,
never raised.
"""

import json
import logging
from datetime import datetime, timezone

from anthropic import AsyncAnthropic

from config import settings
from models.conversation import Conversation
from models.chat_message import ChatMessage as ChatMessageModel
from models.database import get_session
from sqlalchemy import select, update

from services.anthropic_health import report_anthropic_call_failure, report_anthropic_call_success

logger = logging.getLogger(__name__)

_MIN_MESSAGES_FOR_SUMMARY = 4
_REGENERATION_THRESHOLD = 2
_MAX_MESSAGES_FOR_PROMPT = 50

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = (
    "You summarize conversations between a user and an AI assistant called Basebase. "
    "Return ONLY valid JSON with two keys:\n"
    '- "overall": A 1-2 sentence summary of the entire conversation so far.\n'
    '- "recent": A 1-2 sentence summary of the most recent exchange(s).\n'
    "Be concise and informative. No markdown, no extra keys."
)


def _should_regenerate(
    current_message_count: int,
    existing_summary: str | None,
) -> bool:
    """Check whether we should (re)generate the summary."""
    if current_message_count < _MIN_MESSAGES_FOR_SUMMARY:
        return False

    if not existing_summary:
        return True

    try:
        parsed = json.loads(existing_summary)
        last_count = parsed.get("message_count_at_generation", 0)
    except (json.JSONDecodeError, TypeError):
        return True

    return (current_message_count - last_count) >= _REGENERATION_THRESHOLD


def _format_messages(messages: list[ChatMessageModel]) -> str:
    """Format messages into a compact text representation for the prompt."""
    lines: list[str] = []
    for msg in messages:
        role = msg.role.upper()
        parts: list[str] = []
        for block in msg.content_blocks or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text = str(block.get("text", ""))
                    if len(text) > 500:
                        text = text[:500] + "..."
                    parts.append(text)
                elif block.get("type") == "tool_use":
                    parts.append(f"[Tool call: {block.get('name', 'unknown')}]")
        if parts:
            lines.append(f"{role}: {' '.join(parts)}")
    return "\n".join(lines)


async def generate_conversation_summary(
    conversation_id: str,
    organization_id: str,
) -> dict | None:
    """
    Generate (or regenerate) an AI summary for a conversation.

    Returns the parsed summary dict on success, None if skipped or on failure.
    """
    try:
        async with get_session(organization_id=organization_id) as session:
            conv = await session.get(Conversation, conversation_id)
            if not conv:
                logger.warning("Summary: conversation %s not found", conversation_id)
                return None

            if not _should_regenerate(conv.message_count, conv.summary):
                return None

            # Load recent messages
            result = await session.execute(
                select(ChatMessageModel)
                .where(ChatMessageModel.conversation_id == conv.id)
                .order_by(ChatMessageModel.created_at.desc())
                .limit(_MAX_MESSAGES_FOR_PROMPT)
            )
            messages = list(reversed(result.scalars().all()))

            if len(messages) < _MIN_MESSAGES_FOR_SUMMARY:
                return None

            current_count = conv.message_count

        # Build prompt
        formatted = _format_messages(messages)
        user_prompt = f"Summarize this conversation:\n\n{formatted}"

        # Call Claude Haiku
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        try:
            response = await client.messages.create(
                model=_MODEL,
                max_tokens=300,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            await report_anthropic_call_success(source="services.conversation_summary.generate_conversation_summary")
        except Exception as exc:
            await report_anthropic_call_failure(
                exc=exc,
                source="services.conversation_summary.generate_conversation_summary",
            )
            raise

        # Parse response
        raw_text = response.content[0].text if response.content else ""
        parsed = json.loads(raw_text)

        summary_data = {
            "overall": parsed.get("overall", ""),
            "recent": parsed.get("recent", ""),
            "message_count_at_generation": current_count,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Persist to DB
        summary_json = json.dumps(summary_data)
        async with get_session(organization_id=organization_id) as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(summary=summary_json)
            )
            await session.commit()

        logger.info(
            "Summary generated for conversation %s (msg_count=%d)",
            conversation_id,
            current_count,
        )
        return summary_data

    except Exception:
        logger.exception("Failed to generate summary for conversation %s", conversation_id)
        return None
