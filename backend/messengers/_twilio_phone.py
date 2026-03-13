"""
Shared base for Twilio phone-based messengers (SMS, WhatsApp, future).

Implements the :class:`BaseMessenger` hooks for:
- User resolution via ``messenger_user_mappings`` with phone-number fallback
- Multi-org resolution with a Redis-backed qualifying question flow
- Conversation management keyed on ``(source, phone, org_id)``
- MMS media download from Twilio CDN
- Segmented reply delivery (≤1600 chars per segment)
- Markdown → plain-text formatting

Concrete subclasses (:class:`SmsMessenger`, :class:`WhatsAppMessenger`)
only need to set ``meta``.
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

from config import settings
from messengers.base import (
    BaseMessenger,
    InboundMessage,
    MessageType,
    OutboundResponse,
)
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.org_member import OrgMember
from models.organization import Organization
from models.user import User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phone-number helpers
# ---------------------------------------------------------------------------

_DIGITS_RE: re.Pattern[str] = re.compile(r"[^\d]")


def _normalise_e164(phone: str) -> str:
    """Best-effort normalisation to E.164."""
    stripped: str = phone.strip()
    if stripped.startswith("+"):
        return stripped
    digits: str = _DIGITS_RE.sub("", stripped)
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}"


# ---------------------------------------------------------------------------
# Markdown → plain text
# ---------------------------------------------------------------------------

_MD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL), r"\1"),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"!\[([^\]]*)\]\([^)]+\)"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r"\1 (\2)"),
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"\*\*\*(.+?)\*\*\*"), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"\*(.+?)\*"), r"\1"),
    (re.compile(r"__(.+?)__"), r"\1"),
    (re.compile(r"_(.+?)_"), r"\1"),
    (re.compile(r"~~(.+?)~~"), r"\1"),
    (re.compile(r"^>\s?", re.MULTILINE), ""),
    (re.compile(r"^[-*]{3,}\s*$", re.MULTILINE), ""),
    (re.compile(r"\n{3,}"), "\n\n"),
]


def _strip_markdown(text: str) -> str:
    """Convert Markdown to plain text suitable for SMS/WhatsApp."""
    result: str = text
    for pattern, replacement in _MD_PATTERNS:
        result = pattern.sub(replacement, result)
    return result.strip()


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------

_TWILIO_MAX_LENGTH: int = 1600


def _split_text(text: str, max_len: int) -> list[str]:
    """Split *text* into chunks of at most *max_len* chars, preferring newlines."""
    segments: list[str] = []
    remaining: str = text
    while remaining:
        if len(remaining) <= max_len:
            segments.append(remaining)
            break
        cut: int = remaining.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, max_len)
        if cut <= 0:
            cut = max_len
        segments.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return segments


# ---------------------------------------------------------------------------
# Twilio media SSRF guard
# ---------------------------------------------------------------------------

def _is_safe_twilio_media_url(url: str) -> bool:
    """Validate that a Twilio media URL is safe to fetch (HTTPS, twilio.com, no private IPs)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme.lower() != "https":
        return False

    hostname: str | None = parsed.hostname
    if not hostname or not hostname.endswith(".twilio.com"):
        return False

    try:
        addrinfo_list = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except Exception as exc:
        logger.warning("Failed to resolve Twilio media host %s: %s", hostname, exc)
        return False

    for family, _, _, _, sockaddr in addrinfo_list:
        ip_str: str | None = sockaddr[0] if family in (socket.AF_INET, socket.AF_INET6) else None
        if not ip_str:
            continue
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_reserved:
            logger.warning("Blocking Twilio media URL %s resolving to disallowed IP %s", url, ip_str)
            return False

    return True


# ---------------------------------------------------------------------------
# Twilio media download
# ---------------------------------------------------------------------------

async def _download_twilio_media(media_items: list[dict[str, str]]) -> list[str]:
    """Download MMS/WhatsApp media from Twilio CDN, return upload IDs."""
    from services.file_handler import MAX_FILE_SIZE, store_file

    account_sid: str | None = settings.TWILIO_ACCOUNT_SID
    auth_token: str | None = settings.TWILIO_AUTH_TOKEN
    if not account_sid or not auth_token:
        logger.warning("Twilio credentials not configured — cannot download media")
        return []

    attachment_ids: list[str] = []
    async with httpx.AsyncClient() as client:
        for i, item in enumerate(media_items):
            url: str = item["url"]
            content_type: str = item.get("content_type", "application/octet-stream")

            if not _is_safe_twilio_media_url(url):
                logger.warning("Skipping unsafe Twilio media URL: %s", url)
                continue

            try:
                resp = await client.get(
                    url,
                    auth=(account_sid, auth_token),
                    follow_redirects=True,
                    timeout=30.0,
                )
                resp.raise_for_status()

                data: bytes = resp.content
                if len(data) > MAX_FILE_SIZE:
                    logger.warning("MMS media %d (%d bytes) exceeds max — skipping", i, len(data))
                    continue

                ext: str = content_type.split("/")[-1].split(";")[0]
                filename: str = f"mms_media_{i}.{ext}"
                stored = store_file(filename=filename, data=data, content_type=content_type)
                attachment_ids.append(stored.upload_id)
                logger.info("Downloaded media %d (%s, %d bytes) → %s", i, content_type, len(data), stored.upload_id)
            except Exception as exc:
                logger.error("Failed to download media %d from %s: %s", i, url, exc)

    return attachment_ids


# ---------------------------------------------------------------------------
# Image artifact extraction (for MMS replies)
# ---------------------------------------------------------------------------

def _build_public_media_url(upload_id: str) -> str | None:
    """Generate a signed public URL for Twilio to fetch media from."""
    from services.file_handler import generate_media_token

    base_url: str | None = settings.TWILIO_WEBHOOK_URL
    if not base_url:
        logger.warning("TWILIO_WEBHOOK_URL not set — cannot generate public media URL")
        return None
    media_base: str = base_url.rsplit("/webhook", 1)[0]
    token: str = generate_media_token(upload_id)
    return f"{media_base}/media/{token}"


def _extract_image_artifacts(chunk: str) -> list[str]:
    """Extract public media URLs from a JSON artifact chunk."""
    from services.file_handler import NATIVE_IMAGE_MIMES, store_file

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
    except Exception as exc:
        logger.error("Failed to process image artifact for MMS: %s", exc)

    return []


# ---------------------------------------------------------------------------
# Redis helpers for multi-org qualifying question
# ---------------------------------------------------------------------------

async def _get_redis() -> Any:
    from api.routes.twilio_events import _get_redis as _twilio_redis
    return await _twilio_redis()


async def _get_pending_org_choice(platform: str, phone: str) -> list[str] | None:
    try:
        client = await _get_redis()
        key: str = f"revtops:{platform}_org_pending:{phone}"
        raw: bytes | None = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.error("Redis error reading pending org choice: %s", exc)
        return None


async def _set_pending_org_choice(platform: str, phone: str, org_ids: list[str]) -> None:
    try:
        client = await _get_redis()
        key: str = f"revtops:{platform}_org_pending:{phone}"
        await client.set(key, json.dumps(org_ids), ex=600)
    except Exception as exc:
        logger.error("Redis error setting pending org choice: %s", exc)


async def _clear_pending_org_choice(platform: str, phone: str) -> None:
    try:
        client = await _get_redis()
        key: str = f"revtops:{platform}_org_pending:{phone}"
        await client.delete(key)
    except Exception as exc:
        logger.error("Redis error clearing pending org choice: %s", exc)


# ---------------------------------------------------------------------------
# User & org lookup helpers
# ---------------------------------------------------------------------------

async def _lookup_user_by_phone(phone: str) -> User | None:
    """Look up a user by normalised phone number (admin session, no RLS)."""
    async with get_admin_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        return result.scalar_one_or_none()


async def _lookup_org_memberships(user_id: UUID) -> list[tuple[UUID, str]]:
    """Return ``[(org_id, org_name), ...]`` for every active org membership."""
    async with get_admin_session() as session:
        result = await session.execute(
            select(OrgMember.organization_id, Organization.name)
            .join(Organization, OrgMember.organization_id == Organization.id)
            .where(OrgMember.user_id == user_id)
            .where(OrgMember.status.in_(("active", "onboarding")))
        )
        return [(row[0], row[1]) for row in result.all()]


async def _find_most_recent_conversation(
    user_id: UUID,
    source: str,
    max_age_hours: int = 24,
) -> Conversation | None:
    """Find the most-recently-updated conversation of *source* type for this user."""
    cutoff: datetime = datetime.utcnow() - timedelta(hours=max_age_hours)
    async with get_admin_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .where(Conversation.source == source)
            .where(Conversation.updated_at >= cutoff)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _create_messenger_user_mapping(
    platform: str,
    external_user_id: str,
    user_id: UUID,
    organization_id: UUID,
    workspace_id: str | None = None,
) -> None:
    """Insert a messenger_user_mapping row (idempotent via ON CONFLICT)."""
    from models.messenger_user_mapping import MessengerUserMapping
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with get_admin_session() as session:
        stmt = pg_insert(MessengerUserMapping).values(
            platform=platform,
            workspace_id=workspace_id,
            external_user_id=external_user_id,
            user_id=user_id,
            organization_id=organization_id,
            match_source="phone_auto",
        ).on_conflict_do_nothing(
            constraint="uq_messenger_user_mappings_platform_ws_extid",
        )
        await session.execute(stmt)
        await session.commit()


# ===========================================================================
# TwilioPhoneMessenger
# ===========================================================================


class TwilioPhoneMessenger(BaseMessenger):
    """Shared base for all Twilio phone-based messengers (SMS, WhatsApp, …).

    Subclasses only need to set ``meta`` — all pipeline methods are
    implemented here.
    """

    # ------------------------------------------------------------------
    # Identity resolution
    # ------------------------------------------------------------------

    async def resolve_user(self, message: InboundMessage) -> User | None:
        phone: str = _normalise_e164(message.external_user_id)
        message.external_user_id = phone  # normalise in-place for downstream

        # 1. Try the generic mapping table (works for all messengers)
        user: User | None = await super().resolve_user(message)
        if user is not None:
            return user

        # 2. Fallback: look up User.phone_number and auto-create mapping
        user = await _lookup_user_by_phone(phone)
        if user is not None:
            memberships: list[tuple[UUID, str]] = await _lookup_org_memberships(user.id)
            for org_id, _ in memberships:
                await _create_messenger_user_mapping(
                    platform=self.meta.slug,
                    external_user_id=phone,
                    user_id=user.id,
                    organization_id=org_id,
                )
        return user

    # ------------------------------------------------------------------
    # Organisation resolution (multi-org picker)
    # ------------------------------------------------------------------

    async def resolve_organization(
        self,
        user: User,
        message: InboundMessage,
    ) -> tuple[str, str] | None:
        from services.sms import send_sms

        phone: str = message.external_user_id
        is_whatsapp: bool = self.meta.slug == "whatsapp"
        memberships: list[tuple[UUID, str]] = await _lookup_org_memberships(user.id)

        if not memberships:
            await send_sms(
                to=phone,
                body="Your account is not associated with any organisation. "
                     "Please contact your administrator.",
                whatsapp=is_whatsapp,
            )
            return None  # caller should return {"status": "rejected"}

        if len(memberships) == 1:
            return str(memberships[0][0]), memberships[0][1]

        # Multi-org flow
        resolved: tuple[str, str, bool] | None = await self._resolve_multi_org(
            phone=phone,
            body=message.text,
            user_id=user.id,
            memberships=memberships,
        )
        if resolved is None:
            return None

        org_id: str
        org_name: str
        body_consumed: bool
        org_id, org_name, body_consumed = resolved
        if body_consumed:
            message.text = ""  # prevent forwarding the selection number
        return org_id, org_name

    async def _resolve_multi_org(
        self,
        phone: str,
        body: str,
        user_id: UUID,
        memberships: list[tuple[UUID, str]],
    ) -> tuple[str, str, bool] | None:
        from services.sms import send_sms

        platform: str = self.meta.slug
        is_whatsapp: bool = platform == "whatsapp"

        pending_org_ids: list[str] | None = await _get_pending_org_choice(platform, phone)
        if pending_org_ids is not None:
            choice: str = body.strip()
            try:
                idx: int = int(choice) - 1
                if 0 <= idx < len(pending_org_ids):
                    chosen_org_id: str = pending_org_ids[idx]
                    await _clear_pending_org_choice(platform, phone)
                    chosen_name: str = next(
                        (name for oid, name in memberships if str(oid) == chosen_org_id),
                        "your organisation",
                    )
                    await send_sms(
                        to=phone,
                        body=f"Got it — chatting as {chosen_name}. Send your message!",
                        whatsapp=is_whatsapp,
                    )
                    return chosen_org_id, chosen_name, True
            except ValueError:
                pass
            await self._send_org_picker(phone, memberships)
            return None

        recent: Conversation | None = await _find_most_recent_conversation(
            user_id=user_id, source=platform, max_age_hours=24,
        )
        if recent is not None and recent.organization_id is not None:
            org_id_str: str = str(recent.organization_id)
            org_name: str = next(
                (name for oid, name in memberships if str(oid) == org_id_str),
                "your organisation",
            )
            return org_id_str, org_name, False

        await self._send_org_picker(phone, memberships)
        return None

    async def _send_org_picker(
        self,
        phone: str,
        memberships: list[tuple[UUID, str]],
    ) -> None:
        from services.sms import send_sms

        lines: list[str] = ["Which organisation would you like to chat with? Reply with a number:"]
        org_ids: list[str] = []
        for i, (org_id, org_name) in enumerate(memberships, start=1):
            lines.append(f"{i}. {org_name}")
            org_ids.append(str(org_id))

        await send_sms(
            to=phone,
            body="\n".join(lines),
            whatsapp=(self.meta.slug == "whatsapp"),
        )
        await _set_pending_org_choice(self.meta.slug, phone, org_ids)

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    async def find_or_create_conversation(
        self,
        organization_id: str,
        user: User,
        message: InboundMessage,
    ) -> Conversation:
        phone: str = message.external_user_id
        source: str = self.meta.slug

        async with get_session(organization_id=organization_id) as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.organization_id == UUID(organization_id))
                .where(Conversation.source == source)
                .where(Conversation.source_channel_id == phone)
            )
            conversation: Conversation | None = result.scalar_one_or_none()

            if conversation is not None:
                if user.id and conversation.user_id is None:
                    conversation.user_id = user.id
                    await session.commit()
                return conversation

            display_name: str = user.name or phone
            conversation = Conversation(
                organization_id=UUID(organization_id),
                user_id=user.id,
                source=source,
                source_channel_id=phone,
                source_user_id=phone,
                type="agent",
                title=f"{self.meta.name} - {display_name}",
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)

            logger.info(
                "[%s] Created conversation %s for phone=%s org=%s",
                source,
                conversation.id,
                phone,
                organization_id,
            )
            return conversation

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    async def download_attachments(self, message: InboundMessage) -> list[str]:
        if not message.raw_attachments:
            return []
        return await _download_twilio_media(message.raw_attachments)

    # ------------------------------------------------------------------
    # Response delivery
    # ------------------------------------------------------------------

    async def send_response(
        self,
        message: InboundMessage,
        response: OutboundResponse,
    ) -> None:
        from services.sms import send_sms

        to: str = message.external_user_id
        text: str = response.text
        media_urls: list[str] | None = response.media_urls or None
        is_whatsapp: bool = self.meta.slug == "whatsapp"

        if not text and not media_urls:
            return

        if len(text) <= _TWILIO_MAX_LENGTH:
            await send_sms(to=to, body=text, media_urls=media_urls, whatsapp=is_whatsapp)
            return

        segments: list[str] = _split_text(text, _TWILIO_MAX_LENGTH)
        for i, segment in enumerate(segments):
            logger.info(
                "[%s] Sending segment %d/%d (%d chars)",
                self.meta.slug, i + 1, len(segments), len(segment),
            )
            await send_sms(
                to=to,
                body=segment,
                media_urls=media_urls if i == 0 else None,
                whatsapp=is_whatsapp,
            )

    # ------------------------------------------------------------------
    # Text formatting
    # ------------------------------------------------------------------

    def format_text(self, markdown: str) -> str:
        return _strip_markdown(markdown)

    # ------------------------------------------------------------------
    # Media extraction override
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_media_from_chunk(chunk: str) -> list[str]:
        return _extract_image_artifacts(chunk)

    # ------------------------------------------------------------------
    # Customisable messages
    # ------------------------------------------------------------------

    def unknown_user_message(self) -> str:
        return (
            "This phone number is not registered with Basebase. "
            "Please add your phone number in your profile settings first."
        )
