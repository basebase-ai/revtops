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

import base64
import ipaddress
import json
import logging
import re
import socket
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from sqlalchemy import select

from agents.orchestrator import ChatOrchestrator
from config import settings
from services.credits import can_use_credits
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

# Precompiled patterns for markdown stripping (order matters)
_MD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL), r"\1"),  # fenced code blocks
    (re.compile(r"`([^`]+)`"), r"\1"),                         # inline code
    (re.compile(r"!\[([^\]]*)\]\([^)]+\)"), r"\1"),           # images
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r"\1 (\2)"),     # links → "text (url)"
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),            # headings
    (re.compile(r"\*\*\*(.+?)\*\*\*"), r"\1"),                # bold+italic
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),                    # bold
    (re.compile(r"\*(.+?)\*"), r"\1"),                        # italic
    (re.compile(r"__(.+?)__"), r"\1"),                        # bold (underscore)
    (re.compile(r"_(.+?)_"), r"\1"),                          # italic (underscore)
    (re.compile(r"~~(.+?)~~"), r"\1"),                        # strikethrough
    (re.compile(r"^>\s?", re.MULTILINE), ""),                 # blockquotes
    (re.compile(r"^[-*]{3,}\s*$", re.MULTILINE), ""),         # horizontal rules
    (re.compile(r"\n{3,}"), "\n\n"),                          # collapse blank lines
]


def _strip_markdown(text: str) -> str:
    """Convert markdown to plain text suitable for SMS."""
    result: str = text
    for pattern, replacement in _MD_PATTERNS:
        result = pattern.sub(replacement, result)
    return result.strip()


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
# MMS media download
# ---------------------------------------------------------------------------

def _is_safe_twilio_media_url(url: str) -> bool:
    """
    Validate that a Twilio media URL is safe to fetch.

    - Must be HTTPS.
    - Hostname must be under the Twilio domain.
    - Resolved IPs must not be private/loopback/link-local/etc.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme.lower() != "https":
        return False

    hostname = parsed.hostname
    if not hostname or not hostname.endswith(".twilio.com"):
        return False

    try:
        addrinfo_list = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except Exception as e:
        logger.warning("[sms_conversations] Failed to resolve Twilio media host %s: %s", hostname, e)
        return False

    for family, _, _, _, sockaddr in addrinfo_list:
        ip_str = sockaddr[0] if family in (socket.AF_INET, socket.AF_INET6) else None
        if not ip_str:
            continue
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False

        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
        ):
            logger.warning(
                "[sms_conversations] Blocking Twilio media URL %s resolving to disallowed IP %s",
                url,
                ip_str,
            )
            return False

    return True


async def _download_twilio_media(
    media_items: list[dict[str, str]],
) -> list[str]:
    """
    Download MMS media from Twilio CDN and store in the temp file store.

    Twilio CDN URLs require Basic Auth with ``account_sid:auth_token``.

    Returns:
        List of ``upload_id`` strings for
        :meth:`ChatOrchestrator.process_message`.
    """
    from services.file_handler import store_file, MAX_FILE_SIZE

    account_sid: str | None = settings.TWILIO_ACCOUNT_SID
    auth_token: str | None = settings.TWILIO_AUTH_TOKEN
    if not account_sid or not auth_token:
        logger.warning("[sms_conversations] Twilio credentials not configured — cannot download MMS media")
        return []

    attachment_ids: list[str] = []

    async with httpx.AsyncClient() as client:
        for i, item in enumerate(media_items):
            url: str = item["url"]
            content_type: str = item.get("content_type", "application/octet-stream")

            # Validate URL points to Twilio CDN and does not resolve to private IPs
            if not _is_safe_twilio_media_url(url):
                logger.warning(
                    "[sms_conversations] Skipping unsafe Twilio media URL: %s",
                    url,
                )
                continue

            try:
                # Use auth= so httpx strips credentials on cross-origin redirects
                resp = await client.get(
                    url,
                    auth=(account_sid, auth_token),
                    follow_redirects=True,
                    timeout=30.0,
                )
                resp.raise_for_status()

                data: bytes = resp.content
                if len(data) > MAX_FILE_SIZE:
                    logger.warning(
                        "[sms_conversations] MMS media %d (%d bytes) exceeds max size — skipping",
                        i, len(data),
                    )
                    continue

                # Derive a filename from content type
                ext: str = content_type.split("/")[-1].split(";")[0]
                filename: str = f"mms_media_{i}.{ext}"

                stored = store_file(filename=filename, data=data, content_type=content_type)
                attachment_ids.append(stored.upload_id)
                logger.info(
                    "[sms_conversations] Downloaded MMS media %d (%s, %d bytes) → %s",
                    i, content_type, len(data), stored.upload_id,
                )
            except Exception as e:
                logger.error(
                    "[sms_conversations] Failed to download MMS media %d from %s: %s",
                    i, url, e,
                )

    return attachment_ids


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
            .where(OrgMember.status.in_(("active", "onboarding")))
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


async def _send_sms_reply(
    to: str,
    text: str,
    media_urls: list[str] | None = None,
) -> None:
    """
    Send one or more SMS/MMS messages, splitting on segment boundaries if the
    reply exceeds Twilio's 1600-character concatenated limit.

    Markdown is stripped before sending since SMS is plain-text only.
    Media URLs are attached to the first segment only (Twilio MMS).
    """
    text = _strip_markdown(text)
    if not text and not media_urls:
        return

    # If it fits in one message, send directly (with media if present)
    if len(text) <= _SMS_MAX_LENGTH:
        await send_sms(to=to, body=text, media_urls=media_urls)
        return

    # Split into segments, preferring line-break boundaries
    # Attach media to the first segment only
    segments: list[str] = _split_text(text, _SMS_MAX_LENGTH)
    for i, segment in enumerate(segments):
        logger.info(
            "[sms_conversations] Sending segment %d/%d (%d chars) to %s",
            i + 1,
            len(segments),
            len(segment),
            to,
        )
        await send_sms(
            to=to,
            body=segment,
            media_urls=media_urls if i == 0 else None,
        )


def _build_public_media_url(upload_id: str) -> str | None:
    """
    Generate a signed public URL for Twilio to fetch media from.

    Uses ``TWILIO_WEBHOOK_URL`` to derive the base (e.g.
    ``https://api.basebase.com/api/twilio/webhook`` →
    ``https://api.basebase.com/api/twilio/media/<token>``).
    """
    from services.file_handler import generate_media_token

    base_url: str | None = settings.TWILIO_WEBHOOK_URL
    if not base_url:
        logger.warning("[sms_conversations] TWILIO_WEBHOOK_URL not set — cannot generate public media URL")
        return None

    # Replace /webhook with /media/<token>
    media_base: str = base_url.rsplit("/webhook", 1)[0]
    token: str = generate_media_token(upload_id)
    return f"{media_base}/media/{token}"


def _extract_image_artifacts(chunk: str) -> list[str]:
    """
    Parse a JSON orchestrator chunk and return public media URLs for any
    image-type artifacts (charts, images).

    If the artifact content is base64-encoded image data, store it in
    file_handler and return a signed public URL for Twilio MMS.
    """
    from services.file_handler import store_file, NATIVE_IMAGE_MIMES

    try:
        payload: dict[str, Any] = json.loads(chunk)
    except (json.JSONDecodeError, ValueError):
        return []

    if payload.get("type") != "artifact":
        return []

    artifact: dict[str, Any] | None = payload.get("artifact")
    if not artifact:
        return []

    mime_type: str | None = artifact.get("mime_type")
    content: str | None = artifact.get("content")
    if not content or not mime_type or mime_type not in NATIVE_IMAGE_MIMES:
        return []

    try:
        data: bytes = base64.b64decode(content)
        ext: str = mime_type.split("/")[-1]
        filename: str = f"artifact_{artifact.get('id', 'unknown')}.{ext}"
        stored = store_file(filename=filename, data=data, content_type=mime_type)
        url: str | None = _build_public_media_url(stored.upload_id)
        if url:
            return [url]
    except Exception as e:
        logger.error("[sms_conversations] Failed to process image artifact for MMS: %s", e)

    return []


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
    media_items: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Process an incoming SMS/MMS message end-to-end.

    1. Normalise phone, look up user
    2. Resolve organisation (single vs multi-org)
    3. Find/create conversation
    4. Download MMS media (if any)
    5. Run through ChatOrchestrator
    6. Reply via SMS/MMS

    Args:
        from_number: Sender phone (E.164)
        to_number: Receiving Twilio number (E.164)
        body: SMS body text
        message_sid: Twilio MessageSid for logging
        media_items: Optional list of MMS media dicts with ``url`` and ``content_type``

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
            body="This phone number is not registered with Basebase. "
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
    body_consumed: bool = False

    if len(memberships) == 1:
        # Fast path: single org
        organization_id = str(memberships[0][0])
        organization_name = memberships[0][1]
    else:
        # Multi-org: check for pending choice first
        resolved: tuple[str, str, bool] | None = await _resolve_multi_org(
            phone=phone,
            body=body,
            user_id=user.id,
            memberships=memberships,
        )
        if resolved is None:
            # We sent a qualifying question — stop processing this message
            return {"status": "pending_org_choice"}
        organization_id, organization_name, body_consumed = resolved

    assert organization_id is not None

    logger.info(
        "[sms_conversations] Resolved org=%s (%s) for user=%s",
        organization_id,
        organization_name,
        user_id,
    )

    # ── 3. Find or create conversation ───────────────────────────────────
    # Always create so that future messages route to this org automatically.
    conversation: Conversation = await find_or_create_sms_conversation(
        organization_id=organization_id,
        phone_number=phone,
        user_id=user_id,
        user_name=user_name,
    )

    if body_consumed:
        # The message was an org-selection number, not a real message.
        # Confirmation already sent via SMS — nothing more to do.
        return {"status": "org_selected", "organization": organization_name}

    if not await can_use_credits(organization_id):
        await _send_sms_reply(
            to=phone,
            text="You're out of credits or don't have an active subscription. Please add a payment method in Basebase to continue.",
        )
        return {"status": "error", "error": "insufficient_credits"}

    # ── 4. Download MMS media (if any) ──────────────────────────────────
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
        source="sms",
    )

    # Collect full response (no streaming for SMS)
    full_response: str = ""
    outbound_media_urls: list[str] = []
    try:
        async for chunk in orchestrator.process_message(
            message_text, attachment_ids=attachment_ids or None,
        ):
            # Check for image artifact chunks that we can send as MMS
            if chunk.startswith("{"):
                outbound_media_urls.extend(_extract_image_artifacts(chunk))
            else:
                full_response += chunk
    except Exception as e:
        logger.exception("[sms_conversations] Orchestrator error: %s", e)
        full_response += (
            "\nSorry, something went wrong processing your message. "
            "Please try again."
        )

    # ── 6. Reply via SMS/MMS ─────────────────────────────────────────────
    response_text: str = full_response.strip()
    if response_text or outbound_media_urls:
        await _send_sms_reply(
            to=phone,
            text=response_text or "",
            media_urls=outbound_media_urls or None,
        )
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
) -> tuple[str, str, bool] | None:
    """
    Resolve which organisation to use when the user belongs to multiple.

    Returns ``(org_id, org_name, body_consumed)`` if resolved, or ``None``
    if we sent a qualifying question and need to wait for the next message.

    *body_consumed* is ``True`` when the message body was used as an org
    selection (e.g. "3") and should NOT be forwarded to the orchestrator.
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
                return chosen_org_id, chosen_name, True  # body consumed
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
        return org_id, org_name, False  # body NOT consumed

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
