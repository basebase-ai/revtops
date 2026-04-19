"""
Async AI-generated conversation summaries and titles.

Summaries are plain text in Conversation.summary. Staleness uses semantic
word counts (text blocks only, excluding tool_use / tool_result / attachment).

This is a non-critical background task — errors are caught and logged, not raised.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from config import settings
from models.chat_message import ChatMessage as ChatMessageModel
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.user import User
from sqlalchemy import select, update

from services.anthropic_health import report_anthropic_call_failure, report_anthropic_call_success
from services.llm_provider import resolve_llm_config, get_adapter

logger = logging.getLogger(__name__)

_SEMANTIC_MIN_WORDS = 100
_SEMANTIC_REGEN_WORD_DELTA = 50
_MAX_MESSAGES_FOR_PROMPT = 50

_TITLE_MAX_CHARS = 80

_SUMMARY_SYSTEM_PROMPT = (
    "You summarize conversations between a human user and an AI assistant called Basebase. "
    "Return ONLY plain text (no JSON, no markdown fences). Write 2–4 short sentences that cover:\n"
    "- Who the user is (use the display name provided if relevant) and what they were trying to accomplish.\n"
    "- Which tools or capabilities the assistant used (tool calls appear as [Tool call: name] in the transcript).\n"
    "- Whether the user got a useful outcome or what was left open.\n"
    "Be concise and factual. The transcript may be truncated for length — do NOT mention truncation, "
    "missing content, or cut-off messages. Summarize only what is present."
)

_TITLE_MIN_WORDS = 20
_GENERIC_TITLE_VALUES = {
    "conversation",
    "new chat",
    "chat",
    "untitled",
    "untitled conversation",
    "title",
}

_TITLE_SYSTEM_PROMPT = (
    "You name chat conversations for a sidebar list. Return ONLY a short title: "
    "5–12 words, no quotes, no trailing punctuation. Mention the user by first name if a name is given. "
    "Describe the main topic or outcome, not the opening greeting.\n"
    "NEVER start with 'Tool call', '[Tool call', or any bracketed prefix.\n"
    "NEVER echo or quote the conversation text. Write a fresh descriptive label.\n"
    "NEVER write the title from the assistant's perspective (no 'I', 'me', 'my')."
)


def _semantic_word_count_from_blocks(blocks: list[dict[str, Any]] | None) -> int:
    """Count words in text-type content blocks only (exclude tool_use, attachments, etc.)."""
    if not blocks:
        return 0
    total: int = 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text_val: str = str(block.get("text") or "").strip()
        if text_val:
            total += len(text_val.split())
    return total


async def count_semantic_words_for_conversation(
    session: Any,
    conversation_id: uuid.UUID,
) -> int:
    """Sum semantic word counts across all messages in the conversation."""
    result = await session.execute(
        select(ChatMessageModel.content_blocks).where(
            ChatMessageModel.conversation_id == conversation_id
        )
    )
    total: int = 0
    for row in result:
        blocks: list[dict[str, Any]] | None = row[0]
        total += _semantic_word_count_from_blocks(blocks)
    return total


def _should_regenerate_summary(
    semantic_words: int,
    existing_summary: str | None,
    summary_word_count_at_generation: int | None,
) -> bool:
    if semantic_words < _SEMANTIC_MIN_WORDS:
        return False
    if not existing_summary or not existing_summary.strip():
        return True
    if summary_word_count_at_generation is None:
        return semantic_words >= _SEMANTIC_MIN_WORDS + _SEMANTIC_REGEN_WORD_DELTA
    return (semantic_words - summary_word_count_at_generation) >= _SEMANTIC_REGEN_WORD_DELTA


def _format_messages(messages: list[ChatMessageModel]) -> str:
    """Format messages into a compact text representation for the prompt."""
    lines: list[str] = []
    for msg in messages:
        role: str = str(msg.role).upper()
        parts: list[str] = []
        for block in msg.content_blocks or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_val = str(block.get("text", ""))
                    if len(text_val) > 2000:
                        text_val = text_val[:2000]
                    parts.append(text_val)
                elif block.get("type") == "tool_use":
                    parts.append(f"[Tool call: {block.get('name', 'unknown')}]")
        if parts:
            lines.append(f"{role}: {' '.join(parts)}")
    return "\n".join(lines)


def _sanitize_title(raw: str) -> str:
    cleaned: str = raw.strip().strip('"').strip("'")
    cleaned = re.sub(r"^\[.*?\]\s*", "", cleaned)
    cleaned = re.sub(r"^Tool call:\s*\w+\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > _TITLE_MAX_CHARS:
        cleaned = cleaned[: _TITLE_MAX_CHARS - 1].rstrip() + "…"
    return cleaned or "Conversation"


def _is_generic_title(title: str) -> bool:
    """Identify low-signal titles that should never overwrite a conversation."""
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    return normalized in _GENERIC_TITLE_VALUES


def _fallback_title_from_formatted_transcript(formatted: str) -> str | None:
    """
    Build a deterministic fallback title from the first user utterance.

    Expected formatted shape:
      USER: ...
      ASSISTANT: ...
    """
    for line in formatted.splitlines():
        if not line.startswith("USER:"):
            continue
        text = line[len("USER:"):].strip()
        if not text:
            continue
        words = text.split()
        fallback = " ".join(words[:8]).strip()
        if len(fallback) > _TITLE_MAX_CHARS:
            fallback = fallback[: _TITLE_MAX_CHARS - 1].rstrip() + "…"
        if fallback and not _is_generic_title(fallback):
            return fallback
    return None


async def generate_conversation_summary(
    conversation_id: str,
    organization_id: str,
) -> str | None:
    """
    Generate or refresh the plain-text summary when semantic word threshold is met.

    Returns the new summary text on success, None if skipped or on failure.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
        formatted: str = ""
        semantic_words: int = 0
        user_display: str = "the user"
        current_summary: str | None = None
        summary_word_count_col: int | None = None

        async with get_admin_session() as session:
            conv = await session.get(Conversation, conv_uuid)
            if not conv:
                logger.warning("Summary: conversation %s not found", conversation_id)
                return None

            semantic_words = await count_semantic_words_for_conversation(session, conv_uuid)
            current_summary = conv.summary
            summary_word_count_col = conv.summary_word_count

            if not _should_regenerate_summary(
                semantic_words,
                current_summary,
                summary_word_count_col,
            ):
                return None

            result = await session.execute(
                select(ChatMessageModel)
                .where(ChatMessageModel.conversation_id == conv.id)
                .order_by(ChatMessageModel.created_at.desc())
                .limit(_MAX_MESSAGES_FOR_PROMPT)
            )
            messages: list[ChatMessageModel] = list(reversed(result.scalars().all()))
            if len(messages) < 1:
                return None

            formatted = _format_messages(messages)
            if conv.user_id:
                u = await session.get(User, conv.user_id)
                if u and u.name and str(u.name).strip():
                    user_display = str(u.name).strip()

        user_prompt: str = (
            f"User display name (for context): {user_display}\n\n"
            f"Conversation transcript:\n\n{formatted}"
        )

        llm_config = await resolve_llm_config(organization_id)
        adapter = get_adapter(llm_config)
        try:
            completed = await adapter.complete(
                model=llm_config.cheap_model,
                system=_SUMMARY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=500,
            )
            await report_anthropic_call_success(
                source="services.conversation_summary.generate_conversation_summary"
            )
        except Exception as exc:
            await report_anthropic_call_failure(
                exc=exc,
                source="services.conversation_summary.generate_conversation_summary",
            )
            raise

        raw_text: str = (completed.content_blocks[0].text or "").strip() if completed.content_blocks else ""
        if not raw_text:
            logger.warning(
                "Conversation summary returned empty content for conversation %s",
                conversation_id,
            )
            return None

        now_utc: datetime = datetime.now(timezone.utc)
        async with get_admin_session() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conv_uuid)
                .values(
                    summary=raw_text,
                    summary_word_count=semantic_words,
                    summary_updated_at=now_utc,
                )
            )
            await session.commit()

        logger.info(
            "Summary generated for conversation %s (semantic_words=%d)",
            conversation_id,
            semantic_words,
        )
        return raw_text

    except Exception:
        logger.exception("Failed to generate summary for conversation %s", conversation_id)
        return None


async def generate_conversation_title(
    conversation_id: str,
    organization_id: str,
) -> str | None:
    """
    Generate an LLM title once semantic threshold is met and title not yet upgraded.

    Returns the new title on success, None if skipped or on failure.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
        formatted: str = ""
        semantic_words: int = 0
        user_display: str = "the user"

        async with get_admin_session() as session:
            conv = await session.get(Conversation, conv_uuid)
            if not conv:
                return None
            if conv.title_llm_upgraded:
                return None

            semantic_words = await count_semantic_words_for_conversation(session, conv_uuid)
            if semantic_words < _TITLE_MIN_WORDS:
                return None

            result = await session.execute(
                select(ChatMessageModel)
                .where(ChatMessageModel.conversation_id == conv.id)
                .order_by(ChatMessageModel.created_at.desc())
                .limit(_MAX_MESSAGES_FOR_PROMPT)
            )
            messages: list[ChatMessageModel] = list(reversed(result.scalars().all()))
            if not messages:
                return None
            formatted = _format_messages(messages)

            if conv.user_id:
                u = await session.get(User, conv.user_id)
                if u and u.name and str(u.name).strip():
                    user_display = str(u.name).strip()

        user_prompt: str = (
            f"User display name: {user_display}\n\nConversation transcript:\n\n{formatted}"
        )

        llm_config = await resolve_llm_config(organization_id)
        adapter = get_adapter(llm_config)
        try:
            completed = await adapter.complete(
                model=llm_config.cheap_model,
                system=_TITLE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=80,
            )
            await report_anthropic_call_success(
                source="services.conversation_summary.generate_conversation_title"
            )
        except Exception as exc:
            await report_anthropic_call_failure(
                exc=exc,
                source="services.conversation_summary.generate_conversation_title",
            )
            raise

        raw: str = (completed.content_blocks[0].text or "").strip() if completed.content_blocks else ""
        title: str = _sanitize_title(raw)
        if not title:
            return None
        if _is_generic_title(title):
            fallback_title = _fallback_title_from_formatted_transcript(formatted)
            if fallback_title:
                logger.warning(
                    "Title model returned generic title for conversation %s; using transcript fallback",
                    conversation_id,
                )
                title = fallback_title
            else:
                logger.warning(
                    "Title model returned generic title for conversation %s; skipping title update",
                    conversation_id,
                )
                return None

        async with get_admin_session() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conv_uuid)
                .values(
                    title=title[:255],
                    title_llm_upgraded=True,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()

        logger.info("LLM title set for conversation %s", conversation_id)
        return title[:255]

    except Exception:
        logger.exception("Failed to generate title for conversation %s", conversation_id)
        return None
