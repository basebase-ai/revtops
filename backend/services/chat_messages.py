"""
Standalone chat message helpers for human-mode flow (save without invoking agent).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update

from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_session
from models.user import User

logger = logging.getLogger(__name__)


async def resolve_agent_responding(
    conversation_id: str,
    organization_id: str,
    mentions: list[dict[str, Any]] | None,
) -> bool:
    """
    Determine whether the agent should respond and update conversation state.

    - Any {"type": "user", "user_id": "..."} is merged into participating_user_ids (invite-by-mention),
      including when combined with @agent so mixed messages still add participants.
    - If mentions contains {"type": "agent"} -> set agent_responding=True, return True.
    - Else if any user mention -> set agent_responding=False, return False.
    - If no mentions -> return current conversation.agent_responding.

    Returns True if the agent should run, False if human-only.
    """
    conv_uuid = UUID(conversation_id)
    org_uuid = UUID(organization_id) if organization_id else None
    mentions = mentions or []

    has_agent_mention = any(m.get("type") == "agent" for m in mentions)
    user_mentions = [m for m in mentions if m.get("type") == "user" and m.get("user_id")]

    async with get_session(organization_id=organization_id) as session:
        row = await session.execute(
            select(Conversation.agent_responding, Conversation.participating_user_ids).where(
                Conversation.id == conv_uuid
            )
        )
        conv_row = row.one_or_none()
        if not conv_row:
            return True  # Conversation not found; default to agent

        current_agent_responding: bool = conv_row[0]
        participating: list[UUID] = list(conv_row[1] or [])

        mentioned_ids: list[UUID] = [
            UUID(m["user_id"]) for m in user_mentions if m.get("user_id")
        ]
        for uid in mentioned_ids:
            if uid not in participating:
                participating.append(uid)

        if has_agent_mention:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conv_uuid)
                .values(agent_responding=True, participating_user_ids=participating)
            )
            await session.commit()
            return True

        if user_mentions:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conv_uuid)
                .values(agent_responding=False, participating_user_ids=participating)
            )
            await session.commit()
            return False

        return current_agent_responding


async def save_user_message(
    conversation_id: str,
    user_id: str,
    organization_id: str,
    message_text: str,
    attachment_ids: list[str] | None = None,
    sender_name: str | None = None,
    sender_email: str | None = None,
) -> str:
    """
    Save a user message to the DB, update conversation cache, broadcast to participants.

    Returns the new message ID (UUID string).
    """
    conv_uuid = UUID(conversation_id)
    user_uuid = UUID(user_id)
    org_uuid = UUID(organization_id) if organization_id else None

    blocks: list[dict[str, Any]] = []
    if attachment_ids:
        for aid in attachment_ids:
            blocks.append({"type": "attachment", "id": aid})
    blocks.append({"type": "text", "text": message_text})

    message_id = uuid4()
    async with get_session(organization_id=organization_id) as session:
        if not sender_name or not sender_email:
            user_row = await session.execute(select(User.name, User.email).where(User.id == user_uuid))
            u = user_row.one_or_none()
            if u:
                sender_name = sender_name or u[0]
                sender_email = sender_email or u[1]

        message = ChatMessage(
            id=message_id,
            conversation_id=conv_uuid,
            user_id=user_uuid,
            organization_id=org_uuid,
            role="user",
            content_blocks=blocks,
            created_at=datetime.utcnow(),
        )
        session.add(message)
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conv_uuid)
            .values(
                updated_at=datetime.utcnow(),
                message_count=Conversation.message_count + 1,
                last_message_preview=(message_text[:200] if message_text else None),
            )
        )
        scope_participants = await session.execute(
            select(Conversation.scope, Conversation.participating_user_ids).where(
                Conversation.id == conv_uuid
            )
        )
        row = scope_participants.one_or_none()
        message_data = message.to_dict(
            sender_name=sender_name,
            sender_email=sender_email,
        )
        await session.commit()

    scope: str = row[0] if row else "private"
    participant_ids: list[str] = [str(uid) for uid in (row[1] or [])] if row else []

    if scope == "shared" and participant_ids:
        from api.websockets import broadcast_conversation_message

        await broadcast_conversation_message(
            conversation_id=conversation_id,
            scope=scope,
            participant_user_ids=participant_ids,
            message_data=message_data,
            sender_user_id=user_id,
        )

    logger.info("[chat_messages] Saved user message to conversation %s", conversation_id)
    return str(message_id)
