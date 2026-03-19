"""
Conversation embeddings for semantic workstream clustering.

Builds a single vector per conversation from title + summary + recent user messages,
stored in conversations.embedding. Staleness is tracked via embedding_message_count.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from models.conversation import Conversation
from models.chat_message import ChatMessage as ChatMessageModel
from models.database import get_session
from models.workstream_snapshot import WorkstreamSnapshot
from sqlalchemy import select, update

from services.embeddings import get_embedding_service

logger = logging.getLogger(__name__)

_STALENESS_THRESHOLD = 2
_MAX_RECENT_CHARS = 12_000
_MAX_MESSAGES_FOR_RECENT = 50


async def _mark_workstream_snapshots_stale(organization_id: str) -> None:
    """Set stale_since on all workstream snapshots for this org so next GET recomputes."""
    try:
        async with get_session(organization_id=organization_id) as session:
            await session.execute(
                update(WorkstreamSnapshot)
                .where(WorkstreamSnapshot.organization_id == UUID(organization_id))
                .values(stale_since=datetime.now(timezone.utc))
            )
            await session.commit()
    except Exception:
        logger.debug("Could not mark workstream snapshots stale for org %s", organization_id, exc_info=True)


def _extract_text_from_blocks(blocks: list[dict[str, Any]] | None) -> str:
    """Extract concatenated text from content_blocks (text and tool_use names only)."""
    if not blocks:
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if text:
                parts.append(str(text))
        elif block.get("type") == "tool_use":
            name = block.get("name")
            if name:
                parts.append(f"[{name}]")
    return " ".join(parts)


def build_embedding_text(
    title: str | None,
    summary_overall: str | None,
    recent_user_texts: list[str],
) -> str:
    """Build a single string for embedding: title + summary + recent user messages."""
    sections: list[str] = []
    if title and title.strip():
        sections.append(f"Title: {title.strip()}")
    if summary_overall and summary_overall.strip():
        sections.append(f"Summary: {summary_overall.strip()}")
    combined_recent = "\n".join(recent_user_texts).strip()
    if combined_recent:
        if len(combined_recent) > _MAX_RECENT_CHARS:
            combined_recent = combined_recent[-_MAX_RECENT_CHARS:]
        sections.append(f"Recent: {combined_recent}")
    text = "\n\n".join(sections).strip()
    return text if text else "Untitled conversation"


async def update_conversation_embedding(
    conversation_id: str,
    organization_id: str,
) -> bool:
    """
    Generate or refresh the conversation embedding if stale.

    Staleness: message_count - embedding_message_count >= _STALENESS_THRESHOLD.
    Builds text from title + summary.overall + last N user messages, then embeds.

    Returns True if the embedding was updated, False if skipped or on failure.
    """
    try:
        async with get_session(organization_id=organization_id) as session:
            conv = await session.get(Conversation, conversation_id)
            if not conv:
                logger.warning("Embedding: conversation %s not found", conversation_id)
                return False

            current_count: int = conv.message_count
            emb_count: int = conv.embedding_message_count
            if (current_count - emb_count) < _STALENESS_THRESHOLD:
                return False

            summary_overall: str | None = None
            if conv.summary:
                try:
                    parsed = json.loads(conv.summary)
                    summary_overall = parsed.get("overall") if isinstance(parsed, dict) else None
                except (json.JSONDecodeError, TypeError):
                    pass

            result = await session.execute(
                select(ChatMessageModel)
                .where(
                    ChatMessageModel.conversation_id == conv.id,
                    ChatMessageModel.role == "user",
                )
                .order_by(ChatMessageModel.created_at.desc())
                .limit(_MAX_MESSAGES_FOR_RECENT)
            )
            user_messages = list(result.scalars().all())
            recent_texts: list[str] = []
            total_chars = 0
            for msg in reversed(user_messages):
                text = _extract_text_from_blocks(msg.content_blocks)
                if not text and msg.content:
                    text = msg.content
                if text:
                    recent_texts.append(text)
                    total_chars += len(text)
                    if total_chars >= _MAX_RECENT_CHARS:
                        break

        embedding_text = build_embedding_text(
            title=conv.title,
            summary_overall=summary_overall,
            recent_user_texts=recent_texts,
        )
        if not embedding_text or embedding_text == "Untitled conversation":
            embedding_text = f"Conversation {conversation_id}"

        service = get_embedding_service()
        vector: list[float] = await service.generate_embedding(embedding_text)

        async with get_session(organization_id=organization_id) as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    embedding=vector,
                    embedding_message_count=current_count,
                )
            )
            await session.commit()

        await _mark_workstream_snapshots_stale(organization_id)
        logger.info(
            "Embedding updated for conversation %s (message_count=%d)",
            conversation_id,
            current_count,
        )
        return True

    except Exception:
        logger.exception("Failed to update embedding for conversation %s", conversation_id)
        return False
