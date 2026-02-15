"""
SMS conversation service.

Handles processing inbound SMS messages (via Twilio) and routing them
through the agent orchestrator.  Mirrors the Slack conversation flow in
``services/slack_conversations.py`` but adapted for the SMS channel.

Flow:
1. Normalise the sender phone number
2. Look up the RevTops user by phone_number (admin session — org unknown)
3. Resolve the organisation (single-org fast path, multi-org qualifying question)
4. Find or create a conversation (source='sms', keyed on phone number + org)
5. Stream the response from the ChatOrchestrator
6. Send the reply back via SMS (split into <=1600 char segments)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from agents.orchestrator import ChatOrchestrator
from config import settings
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.org_member import OrgMember
from models.organization import Organization
from models.user import User
from services.sms import send_sms

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phone number helpers
# ---------------------------------------------------------------------------

_DIGITS_RE: re.Pattern[str] = re.compile(r"[^\d]")


def _normalise_e164(phone: str) -> str:
    """
    Best-effort normalisation to E.164.

    Already-valid numbers ("+1...") pass through.  Bare 10-digit US numbers
    get a +1 prefix.
    """
    stripped: str = phone.strip()
    if stripped.startswith("+"):
        return stripped
    digits: str = _DIGITS_RE.sub("", stripped)
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}"


# ---------------------------------------------------------------------------
# Redis helpers for multi-org qualifying question
# ---------------------------------------------------------------------------

async def _get_redis() -> Any:
    """Lazy import to share the Redis client from twilio_events."""
    from api.routes.twilio_events import _get_redis as _twilio_redis
    return await _twilio_redis()


async def _get_pending_org_choice(phone: str) -> list[str] | None:
    """
    Return the list of org IDs stored for a pending multi-org SMS prompt,
    or None if no pending prompt exists.
    """
    try:
        client = await _get_redis()
        key: str = f"revtops:sms_org_pending:{phone}"
        raw: bytes | None = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.error("[sms_conversations] Redis error reading pending org choice: %s", e)
        return None


async def _set_pending_org_choice(phone: str, org_ids: list[str]) -> None:
    """Store the org-choice list in Redis with a 10-minute TTL."""
    try:
        client = await _get_redis()
        key: str = f"revtops:sms_org_pending:{phone}"
        await client.set(key, json.dumps(org_ids), ex=600)  # 10 min
    except Exception as e:
        logger.error("[sms_conversations] Redis error setting pending org choice: %s", e)


async def _clear_pending_org_choice(phone: str) -> None:
    """Delete the pending org-choice key."""
    try:
        client = await _get_redis()
        key: str = f"revtops:sms_org_pending:{phone}"
        await client.delete(key)
    except Exception as e:
        logger.error("[sms_conversations] Redis error clearing pending org choice: %s", e)


# ---------------------------------------------------------------------------
# User & org resolution
# ---------------------------------------------------------------------------

async def _lookup_user_by_phone(phone: str) -> User | None:
    """Look up a user by normalised phone number (admin session, no RLS)."""
    async with get_admin_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        return result.scalar_one_or_none()


async def _lookup_org_memberships(user_id: UUID) -> list[tuple[UUID, str]]:
    """
    Return ``[(org_id, org_name), ...]`` for every active org membership.
    """
    async with get_admin_session() as session:
        result = await session.execute(
            select(OrgMember.organization_id, Organization.name)
            .join(Organization, OrgMember.organization_id == Organization.id)
            .where(OrgMember.user_id == user_id)
            .where(OrgMember.status == "active")
        )
        return [(row[0], row[1]) for row in result.all()]


async def _find_most_recent_sms_conversation(
    user_id: UUID,
    max_age_hours: int = 24,
) -> Conversation | None:
    """
    Find the most-recently-updated SMS conversation for this user across
    all organisations.  Returns None if nothing within *max_age_hours*.
    """
    cutoff: datetime = datetime.utcnow() - timedelta(hours=max_age_hours)
    async with get_admin_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .where(Conversation.source == "sms")
            .where(Conversation.updated_at >= cutoff)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------

async def find_or_create_sms_conversation(
    organization_id: str,
    phone_number: str,
    user_id: str,
    user_name: str | None = None,
) -> Conversation:
    """
    Find an existing SMS conversation or create a new one.

    Conversations are keyed on ``(source='sms', source_channel_id=phone,
    organization_id=org_id)``.
    """
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.organization_id == UUID(organization_id))
            .where(Conversation.source == "sms")
            .where(Conversation.source_channel_id == phone_number)
        )
        conversation: Conversation | None = result.scalar_one_or_none()

        if conversation is not None:
            # Back-fill user_id if it was missing (shouldn't happen, but defensive)
            if user_id and conversation.user_id is None:
                conversation.user_id = UUID(user_id)
                await session.commit()
            return conversation

        # Create a new SMS conversation
        display_name: str = user_name or phone_number
        conversation = Conversation(
            organization_id=UUID(organization_id),
            user_id=UUID(user_id),
            source="sms",
            source_channel_id=phone_number,
            source_user_id=phone_number,
            type="agent",
            title=f"SMS - {display_name}",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        logger.info(
            "[sms_conversations] Created new conversation %s for phone=%s org=%s",
            conversation.id,
            phone_number,
            organization_id,
        )
        return conversation


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

_SMS_MAX_LENGTH: int = 1600


async def _send_sms_reply(to: str, text: str) -> None:
    """
    Send one or more SMS messages, splitting on segment boundaries if the
    reply exceeds Twilio's 1600-character concatenated limit.
    """
    if not text.strip():
        return

    # If it fits in one message, send directly
    if len(text) <= _SMS_MAX_LENGTH:
        await send_sms(to=to, body=text)
        return

    # Split into segments, preferring line-break boundaries
    segments: list[str] = _split_text(text, _SMS_MAX_LENGTH)
    for i, segment in enumerate(segments):
        logger.info(
            "[sms_conversations] Sending segment %d/%d (%d chars) to %s",
            i + 1,
            len(segments),
            len(segment),
            to,
        )
        await send_sms(to=to, body=segment)


def _split_text(text: str, max_len: int) -> list[str]:
    """Split *text* into chunks of at most *max_len* chars, preferring newlines."""
    segments: list[str] = []
    remaining: str = text
    while remaining:
        if len(remaining) <= max_len:
            segments.append(remaining)
            break
        # Try to break at a newline
        cut: int = remaining.rfind("\n", 0, max_len)
        if cut <= 0:
            # Fall back to space
            cut = remaining.rfind(" ", 0, max_len)
        if cut <= 0:
            # Hard cut
            cut = max_len
        segments.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return segments


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def process_inbound_sms(
    from_number: str,
    to_number: str,
    body: str,
    message_sid: str,
) -> dict[str, Any]:
    """
    Process an incoming SMS message end-to-end.

    1. Normalise phone, look up user
    2. Resolve organisation (single vs multi-org)
    3. Find/create conversation
    4. Run through ChatOrchestrator
    5. Reply via SMS

    Args:
        from_number: Sender phone (E.164)
        to_number: Receiving Twilio number (E.164)
        body: SMS body text
        message_sid: Twilio MessageSid for logging

    Returns:
        Result dict with status and details
    """
    phone: str = _normalise_e164(from_number)
    logger.info(
        "[sms_conversations] Processing inbound SMS from=%s sid=%s body=%s",
        phone,
        message_sid,
        body[:80],
    )

    # ── 1. Look up user ──────────────────────────────────────────────────
    user: User | None = await _lookup_user_by_phone(phone)
    if user is None:
        logger.info("[sms_conversations] No user found for phone=%s", phone)
        await send_sms(
            to=phone,
            body="This phone number is not registered with RevTops. "
                 "Please add your phone number in your profile settings first.",
        )
        return {"status": "rejected", "reason": "unknown_phone"}

    user_id: str = str(user.id)
    user_name: str | None = user.name
    user_email: str | None = user.email

    # ── 2. Resolve organisation ──────────────────────────────────────────
    memberships: list[tuple[UUID, str]] = await _lookup_org_memberships(user.id)

    if not memberships:
        logger.warning(
            "[sms_conversations] User %s has no active org memberships", user_id,
        )
        await send_sms(
            to=phone,
            body="Your account is not associated with any organisation. "
                 "Please contact your administrator.",
        )
        return {"status": "rejected", "reason": "no_org_membership"}

    organization_id: str | None = None
    organization_name: str | None = None

    if len(memberships) == 1:
        # Fast path: single org
        organization_id = str(memberships[0][0])
        organization_name = memberships[0][1]
    else:
        # Multi-org: check for pending choice first
        resolved: tuple[str, str] | None = await _resolve_multi_org(
            phone=phone,
            body=body,
            user_id=user.id,
            memberships=memberships,
        )
        if resolved is None:
            # We sent a qualifying question — stop processing this message
            return {"status": "pending_org_choice"}
        organization_id, organization_name = resolved

    assert organization_id is not None

    logger.info(
        "[sms_conversations] Resolved org=%s (%s) for user=%s",
        organization_id,
        organization_name,
        user_id,
    )

    # ── 3. Find or create conversation ───────────────────────────────────
    conversation: Conversation = await find_or_create_sms_conversation(
        organization_id=organization_id,
        phone_number=phone,
        user_id=user_id,
        user_name=user_name,
    )

    # ── 4. Run through orchestrator ──────────────────────────────────────
    orchestrator = ChatOrchestrator(
        user_id=user_id,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=user_email,
        source_user_id=phone,
        source_user_email=user_email,
        workflow_context=None,
        source="sms",
    )

    # Collect full response (no streaming for SMS)
    full_response: str = ""
    try:
        async for chunk in orchestrator.process_message(body):
            # Skip tool-call JSON chunks — only collect text
            if not chunk.startswith("{"):
                full_response += chunk
    except Exception as e:
        logger.exception("[sms_conversations] Orchestrator error: %s", e)
        full_response += (
            "\nSorry, something went wrong processing your message. "
            "Please try again."
        )

    # ── 5. Reply via SMS ─────────────────────────────────────────────────
    response_text: str = full_response.strip()
    if response_text:
        await _send_sms_reply(to=phone, text=response_text)
    else:
        logger.warning(
            "[sms_conversations] Empty response for conversation=%s",
            conversation.id,
        )

    logger.info(
        "[sms_conversations] Replied to %s (%d chars) conversation=%s",
        phone,
        len(response_text),
        conversation.id,
    )
    return {
        "status": "success",
        "conversation_id": str(conversation.id),
        "response_length": len(response_text),
    }


# ---------------------------------------------------------------------------
# Multi-org resolution
# ---------------------------------------------------------------------------

async def _resolve_multi_org(
    phone: str,
    body: str,
    user_id: UUID,
    memberships: list[tuple[UUID, str]],
) -> tuple[str, str] | None:
    """
    Resolve which organisation to use when the user belongs to multiple.

    Returns ``(org_id, org_name)`` if resolved, or ``None`` if we sent a
    qualifying question and need to wait for the next message.
    """
    # 1. Check for a pending org choice prompt
    pending_org_ids: list[str] | None = await _get_pending_org_choice(phone)
    if pending_org_ids is not None:
        choice: str = body.strip()
        try:
            idx: int = int(choice) - 1
            if 0 <= idx < len(pending_org_ids):
                chosen_org_id: str = pending_org_ids[idx]
                await _clear_pending_org_choice(phone)
                # Find name from memberships
                chosen_name: str = next(
                    (name for oid, name in memberships if str(oid) == chosen_org_id),
                    "your organisation",
                )
                await send_sms(
                    to=phone,
                    body=f"Got it — chatting as {chosen_name}. Send your message!",
                )
                return chosen_org_id, chosen_name
        except ValueError:
            pass
        # Invalid choice — re-send the prompt
        await _send_org_picker(phone, memberships)
        return None

    # 2. Check for a recent SMS conversation in any org
    recent: Conversation | None = await _find_most_recent_sms_conversation(
        user_id=user_id, max_age_hours=24,
    )
    if recent is not None and recent.organization_id is not None:
        org_id: str = str(recent.organization_id)
        org_name: str = next(
            (name for oid, name in memberships if str(oid) == org_id),
            "your organisation",
        )
        return org_id, org_name

    # 3. No recent conversation — send a qualifying question
    await _send_org_picker(phone, memberships)
    return None


async def _send_org_picker(
    phone: str,
    memberships: list[tuple[UUID, str]],
) -> None:
    """Send an SMS asking the user to pick an organisation."""
    lines: list[str] = ["Which organisation would you like to chat with? Reply with a number:"]
    org_ids: list[str] = []
    for i, (org_id, org_name) in enumerate(memberships, start=1):
        lines.append(f"{i}. {org_name}")
        org_ids.append(str(org_id))

    await send_sms(to=phone, body="\n".join(lines))
    await _set_pending_org_choice(phone, org_ids)
