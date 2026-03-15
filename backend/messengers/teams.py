"""
Microsoft Teams messenger — platform-specific hooks for :class:`WorkspaceMessenger`.

Uses the Bot Framework for delivery and Microsoft Graph for user resolution.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from connectors import teams as teams_connector
from messengers._workspace import WorkspaceMessenger
from messengers.base import InboundMessage, MessengerMeta, OutboundResponse, ResponseMode

logger = logging.getLogger(__name__)

# Short-lived cache so post_message can resolve service_url/bot_id when base calls it without context.
_TEAMS_CONTEXT_CACHE_TTL_SECONDS: float = 120.0
_teams_context_cache: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}


def _set_teams_conversation_context(
    workspace_id: str,
    channel_id: str,
    service_url: str,
    bot_id: str | None,
) -> None:
    """Set service_url and bot_id for a conversation (called from route when building message)."""
    key: tuple[str, str] = (workspace_id, channel_id)
    _teams_context_cache[key] = (
        {"service_url": service_url, "bot_id": bot_id},
        time.monotonic() + _TEAMS_CONTEXT_CACHE_TTL_SECONDS,
    )


def _get_teams_conversation_context(
    workspace_id: str | None,
    channel_id: str,
) -> tuple[str | None, str | None]:
    """Get service_url and bot_id for a conversation. Returns (None, None) if missing or expired."""
    if not workspace_id or not channel_id:
        return (None, None)
    key = (workspace_id, channel_id)
    entry = _teams_context_cache.get(key)
    if entry is None:
        return (None, None)
    ctx, expiry = entry
    if time.monotonic() > expiry:
        _teams_context_cache.pop(key, None)
        return (None, None)
    return (ctx.get("service_url"), ctx.get("bot_id"))


class TeamsMessenger(WorkspaceMessenger):
    meta = MessengerMeta(
        name="Microsoft Teams",
        slug="teams",
        response_mode=ResponseMode.STREAMING,
        description="Microsoft Teams chat (DMs, channels, threads, mentions)",
    )

    # ------------------------------------------------------------------
    # Platform-specific hooks
    # ------------------------------------------------------------------

    async def fetch_user_info(
        self,
        workspace_id: str,
        external_user_id: str,
    ) -> dict[str, Any] | None:
        """Fetch user profile from Microsoft Graph (activity.from.aadObjectId)."""
        try:
            return await teams_connector.get_user_info(workspace_id, external_user_id)
        except Exception as exc:
            logger.warning(
                "[teams] Failed to fetch user info for %s: %s",
                external_user_id,
                exc,
            )
            return None

    async def post_message(
        self,
        channel_id: str,
        text: str,
        thread_id: str | None = None,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
        service_url: str | None = None,
        bot_id: str | None = None,
    ) -> str | None:
        """Post a message via the Bot Framework connector."""
        if not service_url or not channel_id:
            service_url, bot_id = _get_teams_conversation_context(
                workspace_id, channel_id
            )
            if not service_url:
                logger.warning("[teams] post_message: missing service_url for channel")
                return None
        return await teams_connector.post_message(
            service_url=service_url,
            conversation_id=channel_id,
            text=text,
            reply_to_id=thread_id,
            bot_id=bot_id,
        )

    async def download_file(
        self,
        file_info: dict[str, Any],
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> tuple[bytes, str, str] | None:
        """Download a Teams attachment (contentUrl)."""
        from services.file_handler import MAX_FILE_SIZE

        content_url: str | None = file_info.get("contentUrl") or file_info.get(
            "content"
        )
        if not content_url:
            return None
        name: str = file_info.get("name", "teams_file")
        content_type: str = file_info.get("contentType") or "application/octet-stream"
        result = await teams_connector.download_teams_file(content_url)
        if result is None:
            return None
        data, filename, ct = result
        if len(data) > MAX_FILE_SIZE:
            logger.warning("[teams] File %s too large (%d bytes)", name, len(data))
            return None
        return (data, filename or name, ct or content_type)

    def format_text(self, markdown: str) -> str:
        """Teams supports a subset of markdown; pass through with minimal normalization."""
        return markdown

    # ------------------------------------------------------------------
    # Typing indicators
    # ------------------------------------------------------------------

    async def add_typing_indicator(self, message: InboundMessage) -> None:
        ctx: dict[str, Any] = message.messenger_context
        service_url: str | None = ctx.get("service_url")
        channel_id: str = ctx.get("channel_id", "")
        bot_id: str | None = ctx.get("bot_id")
        if not service_url or not channel_id:
            return
        try:
            await teams_connector.send_typing_indicator(
                service_url=service_url,
                conversation_id=channel_id,
                bot_id=bot_id,
            )
        except Exception as exc:
            logger.debug("[teams] add_typing_indicator failed: %s", exc)

    async def remove_typing_indicator(self, message: InboundMessage) -> None:
        """Teams does not support removing typing; no-op."""

    # ------------------------------------------------------------------
    # Profile helpers
    # ------------------------------------------------------------------

    def _extract_email_from_profile(self, profile: dict[str, Any]) -> str | None:
        mail: str = (profile.get("mail") or "").strip().lower()
        if mail:
            return mail
        upn: str = (profile.get("userPrincipalName") or "").strip().lower()
        return upn if upn else None

    def unknown_user_message(self) -> str:
        return (
            "I couldn't link your Microsoft Teams identity to a Basebase account. "
            "Please verify your email in Basebase or ask your admin to link your Teams user."
        )

    # ------------------------------------------------------------------
    # Override to set conversation context for post_message and to pass service_url/bot_id
    # ------------------------------------------------------------------

    async def process_inbound(self, message: InboundMessage) -> dict[str, Any]:
        ctx: dict[str, Any] = message.messenger_context
        ws: str | None = ctx.get("workspace_id")
        ch: str = ctx.get("channel_id", "")
        svc: str | None = ctx.get("service_url")
        bot: str | None = ctx.get("bot_id")
        if ws and ch and svc:
            _set_teams_conversation_context(ws, ch, svc, bot)
        return await super().process_inbound(message)

    async def send_response(
        self,
        message: InboundMessage,
        response: OutboundResponse,
    ) -> None:
        ctx: dict[str, Any] = message.messenger_context
        if not response.text:
            return
        await self.post_message(
            channel_id=ctx.get("channel_id", ""),
            text=response.text,
            thread_id=ctx.get("thread_id") or ctx.get("thread_ts"),
            workspace_id=ctx.get("workspace_id"),
            organization_id=ctx.get("organization_id"),
            service_url=ctx.get("service_url"),
            bot_id=ctx.get("bot_id"),
        )
