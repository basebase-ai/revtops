"""
Standalone chat message helpers for human-mode flow (save without invoking agent).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update

from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_session
from models.org_member import OrgMember
from models.user import User

logger = logging.getLogger(__name__)


def is_internal_system_account(email: str | None, name: str | None) -> bool:
    """Return True if this account looks like an internal system/guest user."""
    email_low = (email or "").lower()
    name_low = (name or "").lower()
    if not email_low:
        return True
    return (
        "guest" in email_low
        or ".basebase.local" in email_low
        or "guest user" in name_low
    )


async def resolve_agent_responding(
    conversation_id: str,
    organization_id: str,
    mentions: list[dict[str, Any]] | None,
    message_text: str | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """
    Determine whether the agent should respond and identify suggested invites.

    - If mentions contains {"type": "agent"} -> set agent_responding=True.
    - Else if any user mention -> set agent_responding=False.
    - If no agent mention -> check message_text for plain-text agent mentions (@basebase).
    
    Returns (should_invoke_agent, suggested_invites).
    suggested_invites is a list of {"id": str, "name": str, "email": str} for people 
    mentioned but not yet in the conversation.
    """
    conv_uuid = UUID(conversation_id)
    mentions = mentions or []

    has_agent_mention = any(m.get("type") == "agent" for m in mentions)
    user_mentions = [m for m in mentions if m.get("type") == "user"]

    # Fallback: check plain text for @basebase if no structured agent mention
    if not has_agent_mention and message_text:
        if "@basebase" in message_text.lower():
            has_agent_mention = True

    async with get_session(organization_id=organization_id) as session:
        row = await session.execute(
            select(Conversation.agent_responding, Conversation.participating_user_ids).where(
                Conversation.id == conv_uuid
            )
        )
        conv_row = row.one_or_none()
        if not conv_row:
            return True, []  # Conversation not found; default to agent

        current_agent_responding: bool = conv_row[0]
        participating: list[UUID] = list(conv_row[1] or [])

        # Build list of user IDs mentioned (structured or plain text)
        mentioned_ids: list[UUID] = []
        has_plain_text_user_mention = False

        # 1. Fetch all org members once to resolve both types of mentions
        stmt = (
            select(User.id, User.name, User.email)
            .join(OrgMember, User.id == OrgMember.user_id)
            .where(OrgMember.organization_id == UUID(organization_id))
        )
        members_res = await session.execute(stmt)
        all_members = members_res.all()
        members_by_id = {m[0]: (m[1], m[2]) for m in all_members}

        # 2. Process structured user mentions (with Guest filter)
        for m in user_mentions:
            uid_raw: Any = m.get("user_id")
            if uid_raw and isinstance(uid_raw, str):
                try:
                    uid = UUID(uid_raw)
                    user_info = members_by_id.get(uid)
                    if user_info:
                        name, email = user_info
                        if not is_internal_system_account(email, name):
                            mentioned_ids.append(uid)
                except (ValueError, TypeError):
                    continue

        # 3. Process plain-text mentions (@Name With Spaces or @email)
        if organization_id and message_text and "@" in message_text:
            text_lower = message_text.lower()
            for m_id, m_name, m_email in all_members:
                if is_internal_system_account(m_email, m_name):
                    continue
                
                # Check for @Name mention (case insensitive)
                if m_name:
                    mention_name = f"@{m_name.lower()}"
                    if mention_name in text_lower:
                        if m_id not in mentioned_ids:
                            mentioned_ids.append(m_id)
                            has_plain_text_user_mention = True
                        continue
                
                # Check for @email mention
                mention_email = f"@{m_email.lower()}"
                if mention_email in text_lower:
                    if m_id not in mentioned_ids:
                        mentioned_ids.append(m_id)
                        has_plain_text_user_mention = True

        # Identify who is mentioned but NOT yet participating
        suggested_invites: list[dict[str, Any]] = []
        for uid in mentioned_ids:
            if uid not in participating:
                user_info = members_by_id.get(uid)
                if user_info:
                    u_name, u_email = user_info
                    suggested_invites.append({
                        "id": str(uid),
                        "name": u_name or u_email,
                        "email": u_email
                    })

        # Update agent_responding state if changed by mentions
        new_responding = current_agent_responding
        if has_agent_mention:
            new_responding = True
        elif user_mentions or has_plain_text_user_mention:
            new_responding = False

        if new_responding != current_agent_responding:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conv_uuid)
                .values(agent_responding=new_responding)
            )
            await session.commit()

        return new_responding, suggested_invites


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
