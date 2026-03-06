"""
WhatsApp conversation service.

Handles processing inbound WhatsApp messages (via Twilio) and routing them
through the agent orchestrator.  Nearly identical to the SMS flow in
``services/sms_conversations.py`` but with ``source='whatsapp'``.

Flow:
1. Normalise the sender phone number
2. Look up the RevTops user by phone_number (admin session — org unknown)
3. Resolve the organisation (single-org fast path, multi-org qualifying question)
4. Find or create a conversation (source='whatsapp', keyed on phone number + org)
5. Stream the response from the ChatOrchestrator
6. Send the reply back via WhatsApp (split into <=1600 char segments)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from agents.orchestrator import ChatOrchestrator
from config import settings
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from services.credits import can_use_credits
from services.sms import send_sms
from services.sms_conversations import (
    _download_twilio_media,
    _extract_image_artifacts,
    _lookup_org_memberships,
    _lookup_user_by_phone,
    _normalise_e164,
    _split_text,
    _strip_markdown,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Redis helpers for multi-org qualifying question
# ---------------------------------------------------------------------------

async def _get_redis() -> Any:
    """Lazy import to share the Redis client from twilio_events."""
    from api.routes.twilio_events import _get_redis as _twilio_redis
    return await _twilio_redis()


async def _get_pending_org_choice(phone: str) -> list[str] | None:
    """Return the list of org IDs for a pending multi-org WhatsApp prompt."""
    try:
        client = await _get_redis()
        key: str = f"revtops:whatsapp_org_pending:{phone}"
        raw: bytes | None = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.error("[whatsapp_conversations] Redis error reading pending org choice: %s", e)
        return None


async def _set_pending_org_choice(phone: str, org_ids: list[str]) -> None:
    """Store the org-choice list in Redis with a 10-minute TTL."""
    try:
        client = await _get_redis()
        key: str = f"revtops:whatsapp_org_pending:{phone}"
        await client.set(key, json.dumps(org_ids), ex=600)
    except Exception as e:
        logger.error("[whatsapp_conversations] Redis error setting pending org choice: %s", e)


async def _clear_pending_org_choice(phone: str) -> None:
    """Delete the pending org-choice key."""
    try:
        client = await _get_redis()
        key: str = f"revtops:whatsapp_org_pending:{phone}"
        await client.delete(key)
    except Exception as e:
        logger.error("[whatsapp_conversations] Redis error clearing pending org choice: %s", e)


# ---------------------------------------------------------------------------
# Conversation lookup
# ---------------------------------------------------------------------------

async def _find_most_recent_whatsapp_conversation(
    user_id: UUID,
    max_age_hours: int = 24,
) -> Conversation | None:
    """Find the most-recently-updated WhatsApp conversation for this user."""
    cutoff: datetime = datetime.utcnow() - timedelta(hours=max_age_hours)
    async with get_admin_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .where(Conversation.source == "whatsapp")
            .where(Conversation.updated_at >= cutoff)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def find_or_create_whatsapp_conversation(
    organization_id: str,
    phone_number: str,
    user_id: str,
    user_name: str | None = None,
) -> Conversation:
    """
    Find an existing WhatsApp conversation or create a new one.

    Keyed on ``(source='whatsapp', source_channel_id=phone, organization_id)``.
    """
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.organization_id == UUID(organization_id))
            .where(Conversation.source == "whatsapp")
            .where(Conversation.source_channel_id == phone_number)
        )
        conversation: Conversation | None = result.scalar_one_or_none()

        if conversation is not None:
            if user_id and conversation.user_id is None:
                conversation.user_id = UUID(user_id)
                await session.commit()
            return conversation

        display_name: str = user_name or phone_number
        conversation = Conversation(
            organization_id=UUID(organization_id),
            user_id=UUID(user_id),
            source="whatsapp",
            source_channel_id=phone_number,
            source_user_id=phone_number,
            type="agent",
            title=f"WhatsApp - {display_name}",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        logger.info(
            "[whatsapp_conversations] Created new conversation %s for phone=%s org=%s",
            conversation.id,
            _redact_phone_number(phone_number),
            organization_id,
        )
        return conversation


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

_WA_MAX_LENGTH: int = 1600


def _redact_phone_number(phone_number: str) -> str:
    """
    Return a redacted representation of a phone number suitable for logging.

    Does not include any portion of the original phone number value to avoid
    leaking sensitive information into logs.
    """
    if not phone_number:
        return ""
    # Do not derive the return value from the input phone number to avoid
    # logging any sensitive user data.
    return "<redacted-phone>"


async def _send_whatsapp_reply(
    to: str,
    text: str,
    media_urls: list[str] | None = None,
) -> None:
    """
    Send one or more WhatsApp messages, splitting on segment boundaries
    if the reply exceeds 1600 characters.

    Markdown is stripped since WhatsApp has its own formatting.
    Media URLs are attached to the first segment only.
    """
    text = _strip_markdown(text)
    if not text and not media_urls:
        return

    if len(text) <= _WA_MAX_LENGTH:
        await send_sms(to=to, body=text, media_urls=media_urls, whatsapp=True)
        return

    segments: list[str] = _split_text(text, _WA_MAX_LENGTH)
    for i, segment in enumerate(segments):
        logger.info(
            "[whatsapp_conversations] Sending segment %d/%d (%d chars) to %s",
            i + 1,
            len(segments),
            len(segment),
            to,
        )
        await send_sms(
            to=to,
            body=segment,
            media_urls=media_urls if i == 0 else None,
            whatsapp=True,
        )


# ---------------------------------------------------------------------------
# Multi-org resolution
# ---------------------------------------------------------------------------

async def _resolve_multi_org(
    phone: str,
    body: str,
    user_id: UUID,
    memberships: list[tuple[UUID, str]],
) -> tuple[str, str, bool] | None:
    """
    Resolve which organisation to use when the user belongs to multiple.

    Returns ``(org_id, org_name, body_consumed)`` or ``None`` if a
    qualifying question was sent.
    """
    pending_org_ids: list[str] | None = await _get_pending_org_choice(phone)
    if pending_org_ids is not None:
        choice: str = body.strip()
        try:
            idx: int = int(choice) - 1
            if 0 <= idx < len(pending_org_ids):
                chosen_org_id: str = pending_org_ids[idx]
                await _clear_pending_org_choice(phone)
                chosen_name: str = next(
                    (name for oid, name in memberships if str(oid) == chosen_org_id),
                    "your organisation",
                )
                await send_sms(
                    to=phone,
                    body=f"Got it — chatting as {chosen_name}. Send your message!",
                    whatsapp=True,
                )
                return chosen_org_id, chosen_name, True
        except ValueError:
            pass
        await _send_org_picker(phone, memberships)
        return None

    recent: Conversation | None = await _find_most_recent_whatsapp_conversation(
        user_id=user_id, max_age_hours=24,
    )
    if recent is not None and recent.organization_id is not None:
        org_id: str = str(recent.organization_id)
        org_name: str = next(
            (name for oid, name in memberships if str(oid) == org_id),
            "your organisation",
        )
        return org_id, org_name, False

    await _send_org_picker(phone, memberships)
    return None


async def _send_org_picker(
    phone: str,
    memberships: list[tuple[UUID, str]],
) -> None:
    """Send a WhatsApp message asking the user to pick an organisation."""
    lines: list[str] = ["Which organisation would you like to chat with? Reply with a number:"]
    org_ids: list[str] = []
    for i, (org_id, org_name) in enumerate(memberships, start=1):
        lines.append(f"{i}. {org_name}")
        org_ids.append(str(org_id))

    await send_sms(to=phone, body="\n".join(lines), whatsapp=True)
    await _set_pending_org_choice(phone, org_ids)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def process_inbound_whatsapp(
    from_number: str,
    to_number: str,
    body: str,
    message_sid: str,
    media_items: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Process an incoming WhatsApp message end-to-end.

    1. Normalise phone, look up user
    2. Resolve organisation (single vs multi-org)
    3. Find/create conversation
    4. Download media (if any)
    5. Run through ChatOrchestrator
    6. Reply via WhatsApp
    """
    from models.user import User

    phone: str = _normalise_e164(from_number)
    logger.info(
        "[whatsapp_conversations] Processing inbound WhatsApp from=%s sid=%s body=%s",
        phone,
        message_sid,
        body[:80],
    )

    # ── 1. Look up user ──────────────────────────────────────────────────
    user: User | None = await _lookup_user_by_phone(phone)
    if user is None:
        logger.info("[whatsapp_conversations] No user found for phone=%s", phone)
        await send_sms(
            to=phone,
            body="This phone number is not registered with RevTops. "
                 "Please add your phone number in your profile settings first.",
            whatsapp=True,
        )
        return {"status": "rejected", "reason": "unknown_phone"}

    user_id: str = str(user.id)
    user_name: str | None = user.name
    user_email: str | None = user.email

    # ── 2. Resolve organisation ──────────────────────────────────────────
    memberships: list[tuple[UUID, str]] = await _lookup_org_memberships(user.id)

    if not memberships:
        logger.warning(
            "[whatsapp_conversations] User %s has no active org memberships", user_id,
        )
        await send_sms(
            to=phone,
            body="Your account is not associated with any organisation. "
                 "Please contact your administrator.",
            whatsapp=True,
        )
        return {"status": "rejected", "reason": "no_org_membership"}

    organization_id: str | None = None
    organization_name: str | None = None
    body_consumed: bool = False

    if len(memberships) == 1:
        organization_id = str(memberships[0][0])
        organization_name = memberships[0][1]
    else:
        resolved: tuple[str, str, bool] | None = await _resolve_multi_org(
            phone=phone,
            body=body,
            user_id=user.id,
            memberships=memberships,
        )
        if resolved is None:
            return {"status": "pending_org_choice"}
        organization_id, organization_name, body_consumed = resolved

    assert organization_id is not None

    logger.info(
        "[whatsapp_conversations] Resolved org=%s (%s) for user=%s",
        organization_id,
        organization_name,
        user_id,
    )

    # ── 3. Find or create conversation ───────────────────────────────────
    conversation: Conversation = await find_or_create_whatsapp_conversation(
        organization_id=organization_id,
        phone_number=phone,
        user_id=user_id,
        user_name=user_name,
    )

    if body_consumed:
        return {"status": "org_selected", "organization": organization_name}

    if not await can_use_credits(organization_id):
        await _send_whatsapp_reply(
            to=phone,
            text="You're out of credits or don't have an active subscription. Please add a payment method in Revtops to continue.",
        )
        return {"status": "error", "error": "insufficient_credits"}

    # ── 4. Download media (if any) ───────────────────────────────────────
    attachment_ids: list[str] = []
    if media_items:
        attachment_ids = await _download_twilio_media(media_items)

    message_text: str = body or ("(see attached files)" if attachment_ids else "")

    # ── 5. Run through orchestrator ──────────────────────────────────────
    orchestrator = ChatOrchestrator(
        user_id=user_id,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=user_email,
        source_user_id=phone,
        source_user_email=user_email,
        workflow_context=None,
        source="whatsapp",
    )

    full_response: str = ""
    outbound_media_urls: list[str] = []
    try:
        async for chunk in orchestrator.process_message(
            message_text, attachment_ids=attachment_ids or None,
        ):
            if chunk.startswith("{"):
                outbound_media_urls.extend(_extract_image_artifacts(chunk))
            else:
                full_response += chunk
    except Exception as e:
        logger.exception("[whatsapp_conversations] Orchestrator error: %s", e)
        full_response += (
            "\nSorry, something went wrong processing your message. "
            "Please try again."
        )

    # ── 6. Reply via WhatsApp ────────────────────────────────────────────
    response_text: str = full_response.strip()
    if response_text or outbound_media_urls:
        await _send_whatsapp_reply(
            to=phone,
            text=response_text or "",
            media_urls=outbound_media_urls or None,
        )
    else:
        logger.warning(
            "[whatsapp_conversations] Empty response for conversation=%s",
            conversation.id,
        )

    logger.info(
        "[whatsapp_conversations] Replied to %s (%d chars) conversation=%s",
        phone,
        len(response_text),
        conversation.id,
    )
    return {
        "status": "success",
        "conversation_id": str(conversation.id),
        "response_length": len(response_text),
    }
