"""
Shared base for workspace/team-based messengers (Slack, Teams, Discord, …).

Implements the common pipeline for messengers where:
- A "workspace" (Slack team, Teams tenant, Discord guild) maps to an org
- Users are identified by a platform-specific ID and resolved via
  ``messenger_user_mappings`` + email/profile fallback
- Conversations are threaded (channel + thread ID → one conversation)
- Responses can be streamed via repeated ``post_message`` calls
- Channel messages are persisted as Activity rows for analytics

Concrete subclasses implement a handful of platform-specific hooks:
``fetch_user_info``, ``post_message``, ``download_file``, ``format_text``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from messengers._stream_breaks import find_safe_break
from messengers.base import (
    BaseMessenger,
    InboundMessage,
    MessageType,
    OutboundResponse,
)
from models.activity import Activity
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.messenger_bot_install import MessengerBotInstall
from models.messenger_user_mapping import MessengerUserMapping
from models.org_member import OrgMember
from models.organization import Organization
from models.user import User
from services.anthropic_health import user_message_for_agent_stream_failure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Streaming constants
# ---------------------------------------------------------------------------

STREAM_FLUSH_CHAR_THRESHOLD: int = 240
STREAM_FLUSH_INTERVAL_SECONDS: float = 0.7
SLOW_REPLY_TIMEOUT_SECONDS: int = 30
SLOW_REPLY_MIN_SECONDS_SINCE_LAST_MESSAGE: float = 5.0
SLOW_REPLY_RETRY_BACKOFF_SECONDS: float = 5.0
SLOW_REPLY_MESSAGE: str = "Still working on this, one moment…"

# ---------------------------------------------------------------------------
# In-memory caches
# ---------------------------------------------------------------------------

_USER_INFO_CACHE_TTL_SECONDS: int = 600
_USER_INFO_CACHE_MAX_ENTRIES: int = 5000
_user_info_cache: dict[tuple[str, str, str], tuple[dict[str, Any] | None, float]] = {}
_user_info_cache_lock: asyncio.Lock = asyncio.Lock()

_WORKSPACE_ORG_CACHE_TTL_SECONDS: int = 300
_workspace_org_cache: dict[tuple[str, str], tuple[str | None, float]] = {}
_workspace_org_cache_lock: asyncio.Lock = asyncio.Lock()

_CHANNEL_NAME_CACHE_TTL_SECONDS: int = 3600  # channel names change rarely
_CHANNEL_NAME_CACHE_MAX_ENTRIES: int = 5000
_channel_name_cache: dict[tuple[str, str], tuple[str | None, float]] = {}
_channel_name_cache_lock: asyncio.Lock = asyncio.Lock()



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _merge_participating_user_ids(
    existing: list[UUID] | None,
    new_user_id: str | None,
) -> list[UUID]:
    """Add *new_user_id* to *existing* list if not already present."""
    current: list[UUID] = list(existing or [])
    if new_user_id:
        uid: UUID = UUID(new_user_id)
        if uid not in current:
            current.append(uid)
    return current


def _resolve_conversation_scope(
    message: InboundMessage,
    revtops_user_id: str | None,
) -> str:
    """Resolve conversation scope for newly created or updated conversations."""
    channel_type: str = (message.messenger_context.get("channel_type") or "").strip().lower()
    channel_id: str = (message.messenger_context.get("channel_id") or "").strip().upper()

    # Slack private channels use channel_type="group" and channel IDs that start
    # with "G". These should remain private in our chat UI.
    is_private_slack_channel: bool = (
        channel_type in {"group", "private_channel"}
        or (
            message.message_type != MessageType.DIRECT
            and channel_id.startswith("G")
        )
    )
    if is_private_slack_channel:
        return "private"

    if message.message_type != MessageType.DIRECT:
        return "shared"

    if channel_type in {"mpim", "groupchat"}:
        return "shared"

    identity_known: bool = bool(revtops_user_id or message.external_user_id)
    return "private" if identity_known else "shared"


def _build_workflow_context_for_message(
    platform_slug: str,
    ctx: dict[str, Any],
) -> dict[str, Any] | None:
    """Build workflow_context for orchestrator from inbound messenger context."""
    workflow_context: dict[str, Any] = dict(ctx.get("workflow_context") or {})

    if platform_slug == "slack":
        slack_channel_id: str | None = ctx.get("channel_id")
        slack_thread_ts: str | None = ctx.get("thread_id") or ctx.get("thread_ts")
        slack_channel_name: str | None = ctx.get("channel_name")

        if slack_channel_id and not workflow_context.get("slack_channel_id"):
            workflow_context["slack_channel_id"] = slack_channel_id
        if slack_thread_ts and not workflow_context.get("slack_thread_ts"):
            workflow_context["slack_thread_ts"] = slack_thread_ts
        if slack_channel_name and not workflow_context.get("slack_channel_name"):
            workflow_context["slack_channel_name"] = slack_channel_name

    return workflow_context or None



# ===========================================================================
# WorkspaceMessenger
# ===========================================================================


class WorkspaceMessenger(BaseMessenger):
    """Shared base for team/guild-based messengers (Slack, Teams, Discord).

    Subclasses must implement the following platform-specific hooks:

    - :meth:`fetch_user_info`
    - :meth:`post_message`
    - :meth:`download_file`
    - :meth:`format_text`

    And optionally:

    - :meth:`add_typing_indicator`
    - :meth:`remove_typing_indicator`
    - :meth:`get_bot_token`
    """

    # ------------------------------------------------------------------
    # Platform-specific hooks (abstract — implemented by subclasses)
    # ------------------------------------------------------------------

    async def fetch_user_info(
        self,
        workspace_id: str,
        external_user_id: str,
    ) -> dict[str, Any] | None:
        """Fetch user profile from the platform API. Returns None on failure."""
        return None

    async def post_message(
        self,
        channel_id: str,
        text: str,
        thread_id: str | None = None,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> str | None:
        """Post a message and return the message ID/timestamp. Must be overridden."""
        raise NotImplementedError

    async def download_file(
        self,
        file_info: dict[str, Any],
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> tuple[bytes, str, str] | None:
        """Download a file. Returns ``(data, filename, content_type)`` or None."""
        return None

    async def format_and_post(
        self,
        channel_id: str,
        thread_id: str | None,
        text_to_send: str,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        """Format text and post to the channel. Override to use blocks (e.g. Slack)."""
        formatted: str = self.format_text(text_to_send)
        await self.post_message(
            channel_id=channel_id,
            text=formatted,
            thread_id=thread_id,
            workspace_id=workspace_id,
            organization_id=organization_id,
        )

    async def fetch_channel_name(
        self,
        workspace_id: str,
        channel_id: str,
    ) -> str | None:
        """Fetch the human-readable channel name from the platform API. Override per platform."""
        return None

    async def enrich_message_context(
        self,
        message: InboundMessage,
        organization_id: str,
    ) -> None:
        """Hook for subclasses to attach platform-specific context to messages.

        Called in ``process_inbound`` before the orchestrator is invoked, so
        subclasses can enrich ``message.messenger_context`` with additional
        fields (e.g. human-readable channel names).
        """

    async def add_typing_indicator(self, message: InboundMessage) -> None:
        """Show a typing/processing indicator (e.g. reaction). No-op by default."""

    async def remove_typing_indicator(self, message: InboundMessage) -> None:
        """Remove the typing/processing indicator. No-op by default."""

    # ------------------------------------------------------------------
    # User resolution
    # ------------------------------------------------------------------

    async def resolve_user(self, message: InboundMessage) -> User | None:
        """Resolve an external workspace user to a RevTops user.

        1. Try ``messenger_user_mappings`` (inherited from BaseMessenger)
        2. Fetch platform profile, try email match, auto-create mapping
        3. Fall back to org guest user if configured
        """
        user: User | None = await super().resolve_user(message)
        if user is not None:
            logger.info(
                "[%s] Resolved user via mapping: user=%s ext=%s",
                self.meta.slug, user.id, message.external_user_id,
            )
            return user

        workspace_id: str | None = message.messenger_context.get("workspace_id")
        if not workspace_id:
            return None

        organization_id: str | None = await self._resolve_org_from_workspace(workspace_id)
        if not organization_id:
            return None

        profile: dict[str, Any] | None = await self.get_cached_user_info(
            workspace_id, message.external_user_id,
        )
        if profile is None:
            logger.warning(
                "[%s] No profile for ext=%s ws=%s — falling back to guest",
                self.meta.slug, message.external_user_id, workspace_id,
            )
            return await self._resolve_guest_user(organization_id)

        email: str | None = self._extract_email_from_profile(profile)
        if email:
            matched_user: User | None = await self._match_user_by_email(
                organization_id, email,
            )
            if matched_user is not None:
                await self._upsert_user_mapping(
                    platform=self.meta.slug,
                    workspace_id=workspace_id,
                    external_user_id=message.external_user_id,
                    user_id=matched_user.id,
                    organization_id=UUID(organization_id),
                    external_email=email,
                    match_source="email",
                )
                logger.info(
                    "[%s] Resolved user via email match: user=%s email=%s",
                    self.meta.slug, matched_user.id, email,
                )
                return matched_user
            logger.warning(
                "[%s] Email %s from profile did not match any org member (org=%s)",
                self.meta.slug, email, organization_id,
            )
        else:
            logger.warning(
                "[%s] No email in profile for ext=%s ws=%s — falling back to guest",
                self.meta.slug, message.external_user_id, workspace_id,
            )

        await self._ensure_unmapped_identity(
            organization_id=organization_id,
            external_user_id=message.external_user_id,
            external_email=email,
        )

        return await self._resolve_guest_user(organization_id)

    def _extract_email_from_profile(self, profile: dict[str, Any]) -> str | None:
        """Extract email from a platform user profile. Override per platform."""
        return None

    async def _match_user_by_email(
        self,
        organization_id: str,
        email: str,
    ) -> User | None:
        """Find an org user whose email matches."""
        async with get_admin_session() as session:
            org_uuid: UUID = UUID(organization_id)
            membership_subq = (
                select(OrgMember.user_id)
                .where(OrgMember.organization_id == org_uuid)
                .where(OrgMember.status.in_(("active", "onboarding")))
            )
            result = await session.execute(
                select(User)
                .where(
                    or_(
                        User.id.in_(membership_subq),
                        and_(
                            User.is_guest.is_(True),
                            User.guest_organization_id == org_uuid,
                        ),
                    )
                )
                .where(User.email == email)
            )
            return result.scalar_one_or_none()

    async def _resolve_guest_user(self, organization_id: str) -> User | None:
        """Fall back to the org's guest user if configured."""
        async with get_admin_session() as session:
            org = await session.get(Organization, UUID(organization_id))
            if not org or not getattr(org, "guest_user_enabled", False) or not getattr(org, "guest_user_id", None):
                return None
            guest: User | None = await session.get(User, org.guest_user_id)
            if guest and getattr(guest, "is_guest", False):
                return guest
            return None

    async def _ensure_unmapped_identity(
        self,
        organization_id: str,
        external_user_id: str,
        external_email: str | None,
    ) -> None:
        """Create an unmapped ExternalIdentityMapping so admins can manually link it."""
        from models.external_identity_mapping import ExternalIdentityMapping

        platform: str = self.meta.slug
        normalized_ext_id: str = external_user_id.strip().upper() if external_user_id else ""
        if not normalized_ext_id:
            return
        try:
            async with get_admin_session() as session:
                existing = await session.execute(
                    select(ExternalIdentityMapping)
                    .where(ExternalIdentityMapping.organization_id == UUID(organization_id))
                    .where(ExternalIdentityMapping.source == platform)
                    .where(ExternalIdentityMapping.external_userid == normalized_ext_id)
                    .limit(1)
                )
                if existing.scalar_one_or_none() is not None:
                    return
                now: datetime = datetime.now(UTC).replace(tzinfo=None)
                mapping = ExternalIdentityMapping(
                    id=_uuid.uuid4(),
                    organization_id=UUID(organization_id),
                    user_id=None,
                    revtops_email=None,
                    external_userid=normalized_ext_id,
                    external_email=external_email,
                    source=platform,
                    match_source="messenger_unmatched",
                    created_at=now,
                    updated_at=now,
                )
                session.add(mapping)
                await session.commit()
                logger.info(
                    "[%s] Created unmapped identity org=%s ext=%s email=%s",
                    platform, organization_id, normalized_ext_id, external_email,
                )
        except Exception:
            logger.debug(
                "[%s] Failed to create unmapped identity org=%s ext=%s",
                platform, organization_id, normalized_ext_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Organisation resolution
    # ------------------------------------------------------------------

    async def resolve_organization(
        self,
        user: User,
        message: InboundMessage,
    ) -> tuple[str, str] | None:
        workspace_id: str | None = message.messenger_context.get("workspace_id")
        if not workspace_id:
            logger.warning("[%s] Missing workspace_id in message context", self.meta.slug)
            return None

        org_id: str | None = await self._resolve_org_from_workspace(workspace_id)
        if org_id is None:
            logger.warning(
                "[%s] No organisation found for workspace %s",
                self.meta.slug,
                workspace_id,
            )
            return None

        async with get_admin_session() as session:
            org = await session.get(Organization, UUID(org_id))
            org_name: str = org.name if org else "Unknown"

        return org_id, org_name

    async def _resolve_org_from_workspace(self, workspace_id: str) -> str | None:
        """Look up organisation from workspace ID using cache + DB."""
        platform: str = self.meta.slug

        now: float = time.monotonic()
        cache_key: tuple[str, str] = (platform, workspace_id)
        async with _workspace_org_cache_lock:
            cached = _workspace_org_cache.get(cache_key)
            if cached and cached[1] > now:
                return cached[0]
            _workspace_org_cache.pop(cache_key, None)

        org_id: str | None = None

        # 1. Check messenger_bot_installs
        async with get_admin_session() as session:
            result = await session.execute(
                select(MessengerBotInstall.organization_id)
                .where(MessengerBotInstall.platform == platform)
                .where(MessengerBotInstall.workspace_id == workspace_id)
            )
            row = result.first()
            if row:
                org_id = str(row[0])

        # 1b. Validate the bot-install org has an active subscription; if not,
        #     check Integration table for an org that does (same workspace may
        #     be connected to a stale org with no credits).
        if org_id is not None:
            async with get_admin_session() as session:
                org_row = await session.execute(
                    select(Organization.subscription_status).where(
                        Organization.id == UUID(org_id)
                    )
                )
                status: str | None = (org_row.scalar_one_or_none() or "")
                if status not in ("active", "trialing"):
                    alt_result = await session.execute(
                        select(Integration.organization_id, Organization.subscription_status)
                        .join(Organization, Organization.id == Integration.organization_id)
                        .where(Integration.connector == platform)
                        .where(Integration.is_active == True)  # noqa: E712
                        .where(Organization.subscription_status.in_(("active", "trialing")))
                    )
                    for alt_row in alt_result:
                        alt_extra_check = await session.execute(
                            select(Integration.extra_data)
                            .where(Integration.organization_id == alt_row[0])
                            .where(Integration.connector == platform)
                            .where(Integration.is_active == True)  # noqa: E712
                        )
                        for (extra_data_row,) in alt_extra_check:
                            stored_ws: str | None = (extra_data_row or {}).get("team_id") or (extra_data_row or {}).get("workspace_id")
                            if stored_ws == workspace_id:
                                logger.info(
                                    "[%s] Bot-install org %s has no active subscription; "
                                    "preferring org %s with status=%s for workspace %s",
                                    platform, org_id, alt_row[0], alt_row[1], workspace_id,
                                )
                                org_id = str(alt_row[0])
                                break
                        if org_id != str(row[0]):
                            break

        # 2. Fall back to Integration table (for Nango-based installs)
        if org_id is None:
            async with get_admin_session() as session:
                result = await session.execute(
                    select(Integration)
                    .where(Integration.connector == platform)
                    .where(Integration.is_active == True)  # noqa: E712
                )
                integrations: list[Integration] = list(result.scalars().all())

                for integration in integrations:
                    extra: dict[str, Any] = integration.extra_data or {}
                    stored_ws_id: str | None = extra.get("team_id") or extra.get("workspace_id") or extra.get("tenant_id")
                    if stored_ws_id and stored_ws_id == workspace_id:
                        org_id = str(integration.organization_id)
                        break

                if org_id is None and len(integrations) == 1:
                    org_id = str(integrations[0].organization_id)

        if org_id is not None:
            expiry: float = time.monotonic() + _WORKSPACE_ORG_CACHE_TTL_SECONDS
            async with _workspace_org_cache_lock:
                _workspace_org_cache[(platform, workspace_id)] = (org_id, expiry)

        return org_id

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    async def _has_existing_conversation(self, message: InboundMessage) -> bool:
        """Check whether a conversation already exists for this message's thread.

        Used to gate thread-reply processing — the bot only responds in
        threads where it is already participating.
        """
        ctx: dict[str, Any] = message.messenger_context
        channel_id: str = ctx.get("channel_id", "")
        thread_id: str | None = ctx.get("thread_id") or ctx.get("thread_ts")
        workspace_id: str | None = ctx.get("workspace_id")
        source: str = self.meta.slug

        if not thread_id or not workspace_id:
            return False

        source_channel_id: str = f"{channel_id}:{thread_id}"
        org_id: str | None = await self._resolve_org_from_workspace(workspace_id)
        if not org_id:
            return False

        async with get_session(organization_id=org_id) as session:
            result = await session.execute(
                select(Conversation.id)
                .where(Conversation.organization_id == UUID(org_id))
                .where(Conversation.source == source)
                .where(Conversation.source_channel_id == source_channel_id)
                .limit(1)
            )
            return result.first() is not None

    async def _mentions_payload_for_resolve_agent(
        self,
        message: InboundMessage,
        organization_id: str,
    ) -> list[dict[str, Any]]:
        """Normalize ``message.mentions`` for :func:`resolve_agent_responding`."""
        if not message.mentions:
            return []
        if self.meta.slug == "slack":
            return await self._slack_mentions_to_resolve_payload(message, organization_id)
        resolved: list[dict[str, Any]] = []
        for raw in message.mentions:
            if raw.get("type") == "agent":
                resolved.append({"type": "agent"})
            elif raw.get("type") == "user":
                uid_any: Any = raw.get("user_id")
                if uid_any is not None and str(uid_any).strip():
                    resolved.append({"type": "user", "user_id": str(uid_any)})
                else:
                    resolved.append({"type": "user"})
        return resolved

    async def _slack_mentions_to_resolve_payload(
        self,
        message: InboundMessage,
        organization_id: str,
    ) -> list[dict[str, Any]]:
        """Map Slack ``external_user_id`` mentions to internal ``user_id`` when possible."""
        ctx: dict[str, Any] = message.messenger_context
        workspace_id: str | None = ctx.get("workspace_id")
        org_uuid: UUID = UUID(organization_id)

        external_ids_ordered: list[str] = []
        for raw in message.mentions:
            if raw.get("type") != "user":
                continue
            ext: Any = raw.get("external_user_id")
            if isinstance(ext, str) and ext.strip():
                if ext not in external_ids_ordered:
                    external_ids_ordered.append(ext)

        id_by_external: dict[str, UUID] = {}
        if external_ids_ordered and workspace_id is not None and workspace_id != "":
            async with get_admin_session() as session:
                stmt = (
                    select(
                        MessengerUserMapping.external_user_id,
                        MessengerUserMapping.user_id,
                        MessengerUserMapping.workspace_id,
                    )
                    .where(MessengerUserMapping.platform == "slack")
                    .where(MessengerUserMapping.organization_id == org_uuid)
                    .where(MessengerUserMapping.external_user_id.in_(external_ids_ordered))
                    .where(
                        or_(
                            MessengerUserMapping.workspace_id == workspace_id,
                            MessengerUserMapping.workspace_id.is_(None),
                        )
                    )
                    .order_by(
                        case(
                            (MessengerUserMapping.workspace_id == workspace_id, 0),
                            else_=1,
                        ),
                        MessengerUserMapping.external_user_id,
                    )
                )
                rows: list[Any] = list((await session.execute(stmt)).all())
                for row in rows:
                    ext_uid: str = str(row[0])
                    user_uuid: UUID = row[1]
                    if ext_uid not in id_by_external:
                        id_by_external[ext_uid] = user_uuid

        out: list[dict[str, Any]] = []
        for raw in message.mentions:
            if raw.get("type") == "agent":
                out.append({"type": "agent"})
                continue
            if raw.get("type") != "user":
                continue
            ext_any: Any = raw.get("external_user_id")
            if isinstance(ext_any, str) and ext_any in id_by_external:
                out.append({"type": "user", "user_id": str(id_by_external[ext_any])})
            else:
                out.append({"type": "user"})
        return out

    async def find_or_create_conversation(
        self,
        organization_id: str,
        user: User,
        message: InboundMessage,
    ) -> str:
        ctx: dict[str, Any] = message.messenger_context
        channel_id: str = ctx.get("channel_id", "")
        thread_id: str | None = ctx.get("thread_id") or ctx.get("thread_ts")
        source: str = self.meta.slug

        source_channel_id: str = f"{channel_id}:{thread_id}" if thread_id else channel_id
        revtops_user_id: str | None = str(user.id) if user.id else None

        async with get_session(organization_id=organization_id, user_id=revtops_user_id) as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.organization_id == UUID(organization_id))
                .where(Conversation.source == source)
                .where(Conversation.source_channel_id == source_channel_id)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .limit(2)
            )
            conversations: list[Conversation] = list(result.scalars().all())
            if len(conversations) > 1:
                logger.warning(
                    "[%s] Multiple conversations found for org=%s source_channel_id=%s ids=%s; using latest",
                    source,
                    organization_id,
                    source_channel_id,
                    [str(conv.id) for conv in conversations],
                )
            conversation: Conversation | None = conversations[0] if conversations else None

            if conversation is not None:
                changed: bool = False
                target_scope: str = _resolve_conversation_scope(message, revtops_user_id)
                if message.external_user_id and conversation.source_user_id != message.external_user_id:
                    conversation.source_user_id = message.external_user_id
                    changed = True

                merged: list[UUID] = _merge_participating_user_ids(
                    conversation.participating_user_ids, revtops_user_id,
                )
                if merged != (conversation.participating_user_ids or []):
                    conversation.participating_user_ids = merged
                    changed = True

                if revtops_user_id and conversation.user_id != UUID(revtops_user_id):
                    logger.info(
                        "[%s] Preserving original conversation owner for %s: existing_user_id=%s incoming_user_id=%s",
                        source,
                        conversation.id,
                        conversation.user_id,
                        revtops_user_id,
                    )

                if conversation.scope != target_scope:
                    conversation.scope = target_scope
                    changed = True

                if changed:
                    await session.commit()
                return str(conversation.id)

            source_label: str = {
                "direct": f"{self.meta.name} DM",
                "mention": f"{self.meta.name} @mention",
                "thread_reply": f"{self.meta.name} Thread",
            }.get(message.message_type.value, self.meta.name)

            user_display: str = user.name or message.external_user_id
            conversation = Conversation(
                organization_id=UUID(organization_id),
                user_id=UUID(revtops_user_id) if revtops_user_id else None,
                source=source,
                source_channel_id=source_channel_id,
                source_user_id=message.external_user_id,
                participating_user_ids=_merge_participating_user_ids([], revtops_user_id),
                scope=_resolve_conversation_scope(message, revtops_user_id),
                type="agent",
                title=f"{source_label} - {user_display}",
            )
            session.add(conversation)
            await session.commit()
            # Avoid session.refresh() after commit — can raise InvalidRequestError with async
            # (id is already set via default=uuid.uuid4)

            logger.info(
                "[%s] Created conversation %s channel=%s user=%s",
                source, conversation.id, source_channel_id, revtops_user_id,
            )
            return str(conversation.id)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    async def download_attachments(self, message: InboundMessage) -> list[str]:
        from services.file_handler import MAX_FILE_SIZE, store_file

        if not message.raw_attachments:
            return []

        workspace_id: str | None = message.messenger_context.get("workspace_id")
        organization_id: str | None = message.messenger_context.get("organization_id")
        attachment_ids: list[str] = []

        for file_info in message.raw_attachments:
            result: tuple[bytes, str, str] | None = await self.download_file(
                file_info,
                workspace_id=workspace_id,
                organization_id=organization_id,
            )
            if result is None:
                continue
            data, filename, content_type = result
            if len(data) > MAX_FILE_SIZE:
                logger.warning("[%s] File %s too large (%d bytes)", self.meta.slug, filename, len(data))
                continue
            stored = store_file(filename=filename, data=data, content_type=content_type)
            attachment_ids.append(stored.upload_id)

        return attachment_ids

    # ------------------------------------------------------------------
    # Response delivery (streaming)
    # ------------------------------------------------------------------

    async def send_response(
        self,
        message: InboundMessage,
        response: OutboundResponse,
    ) -> None:
        ctx: dict[str, Any] = message.messenger_context
        channel_id: str = ctx.get("channel_id", "")
        thread_id: str | None = ctx.get("thread_id") or ctx.get("thread_ts")
        workspace_id: str | None = ctx.get("workspace_id")
        organization_id: str | None = ctx.get("organization_id")

        if not response.text:
            return

        await self.post_message(
            channel_id=channel_id,
            text=response.text,
            thread_id=thread_id,
            workspace_id=workspace_id,
            organization_id=organization_id,
        )

    async def stream_and_post_responses(
        self,
        orchestrator: Any,
        message: InboundMessage,
        message_text: str,
        attachment_ids: list[str] | None = None,
        organization_id: str | None = None,
        on_message_posted: Any | None = None,
    ) -> tuple[int, bool, str | None]:
        """Stream orchestrator output and post text segments incrementally.

        Flushes happen when a tool-call boundary arrives, the buffer reaches
        ``STREAM_FLUSH_CHAR_THRESHOLD``, or ``STREAM_FLUSH_INTERVAL_SECONDS``
        elapsed since last flush.

        Returns:
            tuple of (total posted character count, query_failed, failure_reason).
        """
        ctx: dict[str, Any] = message.messenger_context
        channel_id: str = ctx.get("channel_id", "")
        thread_id: str | None = ctx.get("thread_id") or ctx.get("thread_ts")
        workspace_id: str | None = ctx.get("workspace_id")

        current_text: str = ""
        total_length: int = 0
        query_failed: bool = False
        failure_reason: str | None = None
        last_flush_at: float = time.monotonic()
        posted_tool_statuses: dict[str, tuple[str, str]] = {}

        async def _flush(*, reason: str, force: bool = False) -> None:
            nonlocal current_text, total_length, last_flush_at
            text_to_send: str = ""
            if force:
                text_to_send = current_text.strip()
                current_text = ""
            else:
                break_idx: int = find_safe_break(current_text, strategy="quickest_safe")
                if break_idx <= 0:
                    return
                text_to_send = current_text[:break_idx].strip()
                current_text = current_text[break_idx:]

            if not text_to_send:
                return

            await self.format_and_post(
                channel_id,
                thread_id,
                text_to_send,
                workspace_id=workspace_id,
                organization_id=organization_id,
            )
            if callable(on_message_posted):
                on_message_posted()
            total_length += len(text_to_send)
            last_flush_at = time.monotonic()

        try:
            async for chunk in orchestrator.process_message(
                message_text, attachment_ids=attachment_ids,
            ):
                if chunk.startswith("{"):
                    await _flush(reason="tool_boundary", force=True)
                    await self._handle_json_chunk(
                        chunk,
                        channel_id,
                        thread_id,
                        workspace_id,
                        organization_id,
                        posted_tool_statuses=posted_tool_statuses,
                        on_message_posted=on_message_posted,
                    )
                else:
                    current_text += chunk
                    buf_len: int = len(current_text)
                    size_flush: bool = buf_len >= STREAM_FLUSH_CHAR_THRESHOLD
                    time_flush: bool = (time.monotonic() - last_flush_at) >= STREAM_FLUSH_INTERVAL_SECONDS
                    if size_flush or time_flush:
                        await _flush(reason="buffer_size" if size_flush else "interval")
        except Exception as exc:
            logger.error("[%s] Error during streaming: %s", self.meta.slug, exc, exc_info=True)
            query_failed = True
            failure_reason = str(exc)
            current_text += user_message_for_agent_stream_failure(exc)

        await _flush(reason="stream_end", force=True)
        return total_length, query_failed, failure_reason

    def format_tool_status_for_display(self, status_text: str) -> str:
        """Format status text for this platform (e.g. Slack may wrap in italics). Default: return as-is."""
        return status_text

    async def _handle_json_chunk(
        self,
        chunk: str,
        channel_id: str,
        thread_id: str | None,
        workspace_id: str | None,
        organization_id: str | None,
        *,
        posted_tool_statuses: dict[str, tuple[str, str]] | None = None,
        on_message_posted: Any | None = None,
    ) -> None:
        """Process a JSON orchestrator chunk (artifacts, apps, etc.). Post tool status when present."""
        try:
            data: dict[str, Any] = json.loads(chunk)
        except (json.JSONDecodeError, TypeError):
            return
        if data.get("type") != "tool_call":
            return
        status_text: str | None = data.get("status_text") if isinstance(data.get("status_text"), str) else None
        if not status_text or not status_text.strip():
            return
        normalized_status_text: str = status_text.strip()
        tool_status: str = data.get("status") if isinstance(data.get("status"), str) else "running"
        global_dedup_key: str = "__last_tool_status_message__"
        if posted_tool_statuses is not None:
            last_global_status: tuple[str, str] | None = posted_tool_statuses.get(global_dedup_key)
            if last_global_status == (tool_status, normalized_status_text):
                logger.info(
                    "[%s] Skipping consecutive duplicate tool status message status=%s text=%s",
                    self.meta.slug,
                    tool_status,
                    normalized_status_text,
                )
                return
        dedup_key: str = (
            data.get("tool_id")
            if isinstance(data.get("tool_id"), str) and data.get("tool_id")
            else data.get("tool_name")
            if isinstance(data.get("tool_name"), str) and data.get("tool_name")
            else normalized_status_text
        )
        last_posted_status: tuple[str, str] | None = None
        if posted_tool_statuses is not None:
            last_posted_status = posted_tool_statuses.get(dedup_key)
        if last_posted_status == (tool_status, normalized_status_text):
            logger.info(
                "[%s] Skipping duplicate tool status message key=%s status=%s text=%s",
                self.meta.slug,
                dedup_key,
                tool_status,
                normalized_status_text,
            )
            return
        message: str = self.format_tool_status_for_display(normalized_status_text)
        if posted_tool_statuses is not None:
            posted_tool_statuses[dedup_key] = (tool_status, normalized_status_text)
            posted_tool_statuses[global_dedup_key] = (tool_status, normalized_status_text)

        async def _post() -> None:
            try:
                await self.post_message(
                    channel_id=channel_id,
                    text=message,
                    thread_id=thread_id,
                    workspace_id=workspace_id,
                    organization_id=organization_id,
                )
                if callable(on_message_posted):
                    on_message_posted()
            except Exception as exc:
                logger.debug("[%s] Tool status message failed: %s", self.meta.slug, exc)

        asyncio.create_task(_post())

    async def _wait_for_slow_reply_window(
        self,
        response_task: asyncio.Task[int],
        get_last_message_sent_at: Any,
    ) -> None:
        """Delay slow-reply notice if we posted too recently, re-checking completion each cycle."""
        while not response_task.done():
            last_message_sent_at: float | None = (
                get_last_message_sent_at() if callable(get_last_message_sent_at) else None
            )
            if last_message_sent_at is None:
                return

            elapsed_since_last_post: float = time.monotonic() - last_message_sent_at
            if elapsed_since_last_post >= SLOW_REPLY_MIN_SECONDS_SINCE_LAST_MESSAGE:
                return

            logger.info(
                "[%s] Delaying slow-reply message; %.2fs since last outbound message (minimum %.2fs)",
                self.meta.slug,
                elapsed_since_last_post,
                SLOW_REPLY_MIN_SECONDS_SINCE_LAST_MESSAGE,
            )
            done, _ = await asyncio.wait(
                {response_task},
                timeout=SLOW_REPLY_RETRY_BACKOFF_SECONDS,
            )
            if response_task in done:
                return

    # ------------------------------------------------------------------
    # Overridden process_inbound with streaming + typing indicator support
    # ------------------------------------------------------------------

    async def process_inbound(self, message: InboundMessage) -> dict[str, Any]:
        """Extended pipeline with typing indicators and streaming delivery."""
        from agents.orchestrator import ChatOrchestrator
        from services.credits import can_use_credits

        result: dict[str, Any] | None = None
        delegated_to_super = False
        caught_error: Exception | None = None
        try:
            # For thread replies, silently ignore if the bot isn't already
            # participating in the thread (no existing conversation).
            if message.message_type == MessageType.THREAD_REPLY:
                has_conversation: bool = await self._has_existing_conversation(message)
                if not has_conversation:
                    logger.info(
                        "[%s] no_existing_thread_conversation channel=%s thread=%s workspace=%s",
                        self.meta.slug,
                        message.messenger_context.get("channel_id"),
                        message.messenger_context.get("thread_id") or message.messenger_context.get("thread_ts"),
                        message.messenger_context.get("workspace_id"),
                    )
                    logger.info(
                        "[%s] Ignoring thread reply — no existing conversation for thread",
                        self.meta.slug,
                    )
                    result = {"status": "ignored", "reason": "no_existing_thread_conversation"}
                    return result

            await self.add_typing_indicator(message)

            user: User | None = await self.resolve_user(message)
            if user is None:
                await self.send_response(message, OutboundResponse(text=self.unknown_user_message()))
                await self.remove_typing_indicator(message)
                result = {"status": "rejected", "reason": "unknown_user"}
                return result

            org_result: tuple[str, str] | None = await self.resolve_organization(user, message)
            if org_result is None:
                await self.remove_typing_indicator(message)
                result = {"status": "error", "error": "no_organization"}
                return result

            organization_id, _organization_name = org_result
            message.messenger_context["organization_id"] = organization_id

            await self.enrich_message_context(message, organization_id)

            if not await can_use_credits(organization_id):
                await self.send_response(message, OutboundResponse(text=self.no_credits_message()))
                await self.remove_typing_indicator(message)
                result = {"status": "error", "error": "insufficient_credits"}
                return result

            conversation_id: str = await self.find_or_create_conversation(
                organization_id, user, message,
            )

            ctx: dict[str, Any] = message.messenger_context
            slack_user_email: str | None = ctx.get("user_email")

            from services.chat_messages import resolve_agent_responding, save_user_message

            should_invoke_agent = True
            if self.meta.slug == "slack" or message.mentions:
                mentions_for_resolve: list[dict[str, Any]] = await self._mentions_payload_for_resolve_agent(
                    message,
                    organization_id,
                )
                should_invoke_agent = await resolve_agent_responding(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    mentions=mentions_for_resolve,
                    message_text=message.text,
                )

            attachment_ids: list[str] = await self.download_attachments(message)
            message_text: str = message.text or ("(see attached files)" if attachment_ids else "")

            if not should_invoke_agent:
                await save_user_message(
                    conversation_id=conversation_id,
                    user_id=str(user.id),
                    organization_id=organization_id,
                    message_text=message_text,
                    attachment_ids=attachment_ids or None,
                    sender_name=user.name,
                    sender_email=slack_user_email or user.email,
                )
                await self.remove_typing_indicator(message)
                result = {"status": "human_only", "conversation_id": conversation_id}
                return result

            workflow_context: dict[str, Any] | None = _build_workflow_context_for_message(
                platform_slug=self.meta.slug,
                ctx=ctx,
            )

            orchestrator = ChatOrchestrator(
                user_id=str(user.id),
                organization_id=organization_id,
                conversation_id=conversation_id,
                user_email=user.email,
                source_user_id=message.external_user_id,
                source_user_email=slack_user_email or user.email,
                workflow_context=workflow_context,
                source=self.meta.slug,
                timezone=ctx.get("timezone"),
                local_time=ctx.get("local_time"),
            )

            if self.meta.response_mode.value == "streaming":
                last_message_sent_at: float | None = None

                def _mark_message_sent() -> None:
                    nonlocal last_message_sent_at
                    last_message_sent_at = time.monotonic()

                response_task: asyncio.Task[int] = asyncio.create_task(
                    self.stream_and_post_responses(
                        orchestrator=orchestrator,
                        message=message,
                        message_text=message_text,
                        attachment_ids=attachment_ids or None,
                        organization_id=organization_id,
                        on_message_posted=_mark_message_sent,
                    )
                )
                done, _ = await asyncio.wait(
                    {response_task}, timeout=SLOW_REPLY_TIMEOUT_SECONDS,
                )
                if response_task in done:
                    total, query_failed, failure_reason = response_task.result()
                    await self.remove_typing_indicator(message)
                    result = {
                        "status": "success",
                        "conversation_id": conversation_id,
                        "response_length": total,
                        "query_failed": query_failed,
                        "failure_reason": failure_reason,
                    }
                    return result

                await self._wait_for_slow_reply_window(
                    response_task=response_task,
                    get_last_message_sent_at=lambda: last_message_sent_at,
                )
                if response_task.done():
                    total, query_failed, failure_reason = response_task.result()
                    await self.remove_typing_indicator(message)
                    result = {
                        "status": "success",
                        "conversation_id": conversation_id,
                        "response_length": total,
                        "query_failed": query_failed,
                        "failure_reason": failure_reason,
                    }
                    return result

                # Slow path: notify user and continue in background
                channel_id: str = ctx.get("channel_id", "")
                thread_id: str | None = ctx.get("thread_id") or ctx.get("thread_ts")
                await self.post_message(
                    channel_id=channel_id,
                    text=SLOW_REPLY_MESSAGE,
                    thread_id=thread_id,
                    workspace_id=ctx.get("workspace_id"),
                    organization_id=organization_id,
                )
                _mark_message_sent()

                async def _finish() -> None:
                    try:
                        await response_task
                    except Exception as exc:
                        logger.error("[%s] Background response failed: %s", self.meta.slug, exc)
                    finally:
                        await self.remove_typing_indicator(message)

                asyncio.create_task(_finish())
                result = {"status": "timeout_continuing", "conversation_id": conversation_id}
                return result

            # Batch mode (fallback — workspace messengers are usually streaming)
            delegated_to_super = True
            result = await super().process_inbound(message)
            return result
        except Exception as exc:
            caught_error = exc
            raise
        finally:
            if not delegated_to_super:
                await self._record_query_outcome(result=result, error=caught_error)

    # ------------------------------------------------------------------
    # Activity persistence
    # ------------------------------------------------------------------

    async def persist_channel_activity(
        self,
        message: InboundMessage,
        organization_id: str,
    ) -> None:
        """Save a non-DM channel message as an Activity row for analytics."""
        ctx: dict[str, Any] = message.messenger_context
        channel_id: str = ctx.get("channel_id", "")
        workspace_id: str | None = ctx.get("workspace_id")
        ts: str = message.message_id
        thread_id: str | None = ctx.get("thread_id") or ctx.get("thread_ts")

        source_id: str = f"{channel_id}:{ts}"

        # Use channel name from enriched context, fall back to resolving it
        channel_name: str | None = ctx.get("channel_name")
        if not channel_name and workspace_id and channel_id:
            channel_name = await self.resolve_channel_name(workspace_id, channel_id)
        subject: str = f"#{channel_name}" if channel_name else f"#{channel_id}"

        activity_date: datetime | None = None
        try:
            activity_date = datetime.utcfromtimestamp(float(ts))
        except (ValueError, TypeError):
            pass

        try:
            async with get_session(organization_id=organization_id) as session:
                custom_fields: dict[str, Any] = {
                    "channel_id": channel_id,
                    "user_id": message.external_user_id,
                    "sender_slack_id": message.external_user_id,
                    "thread_ts": thread_id,
                }
                raw_files: Any = message.raw_attachments or []
                if isinstance(raw_files, list):
                    persisted_files: list[dict[str, Any]] = [
                        file_data
                        for file_data in raw_files
                        if isinstance(file_data, dict)
                    ]
                    if persisted_files:
                        custom_fields["files"] = persisted_files
                        logger.debug(
                            "[%s] Persisting %d attachment(s) in activity cache source_id=%s",
                            self.meta.slug,
                            len(persisted_files),
                            source_id,
                        )
                if channel_name:
                    custom_fields["channel_name"] = channel_name

                stmt = pg_insert(Activity).values(
                    id=_uuid.uuid4(),
                    organization_id=UUID(organization_id),
                    source_system=self.meta.slug,
                    source_id=source_id,
                    type=f"{self.meta.slug}_message",
                    subject=subject,
                    description=message.text[:1000] if message.text else "",
                    activity_date=activity_date,
                    custom_fields=custom_fields,
                    synced_at=datetime.utcnow(),
                ).on_conflict_do_nothing(
                    index_elements=["organization_id", "source_system", "source_id"],
                    index_where=text("source_id IS NOT NULL"),
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as exc:
            logger.error(
                "[%s] Failed to persist activity for %s: %s",
                self.meta.slug, source_id, exc,
            )

    # ------------------------------------------------------------------
    # User info caching
    # ------------------------------------------------------------------

    async def get_cached_user_info(
        self,
        workspace_id: str,
        external_user_id: str,
    ) -> dict[str, Any] | None:
        """Get user info with in-memory caching."""
        cache_key: tuple[str, str, str] = (self.meta.slug, workspace_id, external_user_id)
        now: float = time.monotonic()

        async with _user_info_cache_lock:
            cached = _user_info_cache.get(cache_key)
            if cached and cached[1] > now:
                return cached[0]
            _user_info_cache.pop(cache_key, None)

        profile: dict[str, Any] | None = await self.fetch_user_info(
            workspace_id, external_user_id,
        )

        ttl: int = _USER_INFO_CACHE_TTL_SECONDS if profile else 120
        async with _user_info_cache_lock:
            if len(_user_info_cache) >= _USER_INFO_CACHE_MAX_ENTRIES:
                expired = [k for k, (_, exp) in _user_info_cache.items() if exp <= now]
                for k in expired:
                    del _user_info_cache[k]
            _user_info_cache[cache_key] = (profile, now + ttl)

        return profile

    # ------------------------------------------------------------------
    # Channel name caching
    # ------------------------------------------------------------------

    async def resolve_channel_name(
        self,
        workspace_id: str,
        channel_id: str,
    ) -> str | None:
        """Get channel name with in-memory caching. Falls back to platform API."""
        cache_key: tuple[str, str] = (workspace_id, channel_id)
        now: float = time.monotonic()

        async with _channel_name_cache_lock:
            cached = _channel_name_cache.get(cache_key)
            if cached and cached[1] > now:
                return cached[0]
            _channel_name_cache.pop(cache_key, None)

        name: str | None = await self.fetch_channel_name(workspace_id, channel_id)

        ttl: int = _CHANNEL_NAME_CACHE_TTL_SECONDS if name else 120
        async with _channel_name_cache_lock:
            if len(_channel_name_cache) >= _CHANNEL_NAME_CACHE_MAX_ENTRIES:
                expired = [k for k, (_, exp) in _channel_name_cache.items() if exp <= now]
                for k in expired:
                    del _channel_name_cache[k]
            _channel_name_cache[cache_key] = (name, now + ttl)

        return name

    # ------------------------------------------------------------------
    # Mapping upsert
    # ------------------------------------------------------------------

    @staticmethod
    async def _upsert_user_mapping(
        platform: str,
        workspace_id: str | None,
        external_user_id: str,
        user_id: UUID,
        organization_id: UUID,
        external_email: str | None = None,
        match_source: str = "auto",
    ) -> None:
        async with get_admin_session() as session:
            stmt = pg_insert(MessengerUserMapping).values(
                platform=platform,
                workspace_id=workspace_id,
                external_user_id=external_user_id,
                user_id=user_id,
                organization_id=organization_id,
                external_email=external_email,
                match_source=match_source,
            ).on_conflict_do_nothing(
                constraint="uq_messenger_user_mappings_platform_ws_extid",
            )
            await session.execute(stmt)
            await session.commit()
