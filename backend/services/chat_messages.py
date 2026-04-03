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

    # Fallback: check plain text for @basebase or other user mentions if structured mentions are incomplete
    text_mentions: list[str] = []
    if message_text:
        # Match @followed_by_alphanumeric_dots_dashes (avoids trailing punctuation like commas)
        text_mentions = re.findall(r"@([\w\.-]+)", message_text)
        
        if not has_agent_mention:
            for tm in text_mentions:
                if tm.lower() == "basebase":
                    has_agent_mention = True
                    break

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

        mentioned_ids: list[UUID] = []
        # 1. Collect IDs from structured user mentions
        for m in user_mentions:
            uid_raw: Any = m.get("user_id")
            if uid_raw and isinstance(uid_raw, str):
                try:
                    mentioned_ids.append(UUID(uid_raw))
                except (ValueError, TypeError):
                    continue

        # 2. Collect IDs from plain-text mentions
        has_plain_text_user_mention = False
        if text_mentions and organization_id:
            # Look for members whose name or email matches the @mention string
            # We filter out "basebase" which is the agent
            filtered_tms = [tm for tm in text_mentions if tm.lower() != "basebase"]
            
            if filtered_tms:
                # Perform bulk query to resolve all mentions at once
                # We search for exact email, exact name, or name as email prefix
                stmt = (
                    select(User.id)
                    .join(OrgMember, User.id == OrgMember.user_id)
                    .where(
                        OrgMember.organization_id == UUID(organization_id),
                        or_(
                            User.email.in_(filtered_tms),
                            User.name.in_(filtered_tms),
                            *[User.email.ilike(f"{tm}@%") for tm in filtered_tms]
                        )
                    )
                )
                res = await session.execute(stmt)
                resolved_uids = [r[0] for r in res.all()]
                for uid in resolved_uids:
                    if uid not in mentioned_ids:
                        mentioned_ids.append(uid)
                        has_plain_text_user_mention = True

        # Identify who is mentioned but NOT yet participating
        suggested_invites: list[dict[str, Any]] = []
        to_check = [uid for uid in mentioned_ids if uid not in participating]
        
        if to_check:
            users_res = await session.execute(
                select(User.id, User.name, User.email).where(User.id.in_(to_check))
            )
            for u_id, u_name, u_email in users_res.all():
                suggested_invites.append({
                    "id": str(u_id),
                    "name": u_name or u_email,
                    "email": u_email
                })

        # Update agent_responding state if changed by mentions
        new_responding = current_agent_responding
        if has_agent_mention:
            new_responding = True
        elif user_mentions or has_plain_text_user_mention:
            # If a user was mentioned (structured or plain text that resolved), disable agent
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
