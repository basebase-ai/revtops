"""
Slack messenger — platform-specific hooks for :class:`WorkspaceMessenger`.

All generic pipeline logic (user resolution, org resolution, conversation
management, streaming delivery, activity persistence) lives in
``_workspace.py``.  This file contains only the Slack-specific API calls
and formatting.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from connectors.slack import SlackConnector, markdown_to_mrkdwn
from messengers._workspace import WorkspaceMessenger
from messengers.base import InboundMessage, MessengerMeta, ResponseMode

logger = logging.getLogger(__name__)

_CHANNEL_NAME_CACHE_TTL_SECONDS: int = 600
_CHANNEL_NAME_CACHE_MAX_ENTRIES: int = 5000
_channel_name_cache: dict[tuple[str, str], tuple[str | None, float]] = {}
_channel_name_cache_lock: asyncio.Lock = asyncio.Lock()


class SlackMessenger(WorkspaceMessenger):
    meta = MessengerMeta(
        name="Slack",
        slug="slack",
        response_mode=ResponseMode.STREAMING,
        description="Slack workspace chat (DMs, mentions, threads)",
    )

    # ------------------------------------------------------------------
    # Platform-specific hooks
    # ------------------------------------------------------------------

    async def fetch_user_info(
        self,
        workspace_id: str,
        external_user_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a Slack user profile via ``users.info``."""
        try:
            connector: SlackConnector = await self._get_connector(workspace_id)
            return await connector.get_user_info(external_user_id)
        except Exception as exc:
            logger.warning(
                "[slack] Failed to fetch user info for %s: %s",
                external_user_id, exc,
            )
            return None

    async def enrich_message_context(
        self,
        message: InboundMessage,
        organization_id: str,
    ) -> None:
        """Attach Slack-specific context such as human-readable channel names."""
        ctx = message.messenger_context
        workspace_id: str | None = ctx.get("workspace_id")
        channel_id: str = ctx.get("channel_id", "")

        if not workspace_id or not channel_id:
            return

        try:
            channel_name: str | None = await self._get_channel_name(
                workspace_id=workspace_id,
                channel_id=channel_id,
                organization_id=organization_id,
            )
        except Exception as exc:
            logger.debug(
                "[slack] Failed to enrich message context with channel name ws=%s channel=%s: %s",
                workspace_id,
                channel_id,
                exc,
            )
            return

        if channel_name:
            # Preserve any existing value set upstream.
            ctx.setdefault("channel_name", f"#{channel_name}")

    async def post_message(
        self,
        channel_id: str,
        text: str,
        thread_id: str | None = None,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> str | None:
        """Post a message to Slack via ``chat.postMessage``."""
        connector: SlackConnector = await self._get_connector(
            workspace_id, organization_id=organization_id,
        )
        result: dict[str, Any] = await connector.post_message(
            channel=channel_id,
            text=text,
            thread_ts=thread_id,
        )
        return result.get("ts")

    async def download_file(
        self,
        file_info: dict[str, Any],
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> tuple[bytes, str, str] | None:
        """Download a Slack file using the bot token."""
        from services.file_handler import MAX_FILE_SIZE

        connector: SlackConnector = await self._get_connector(
            workspace_id, organization_id=organization_id,
        )
        url_private: str | None = file_info.get("url_private_download") or file_info.get("url_private")
        if not url_private:
            return None

        filename: str = file_info.get("name", "slack_file")
        content_type: str = file_info.get("mimetype", "application/octet-stream")
        size: int = file_info.get("size", 0)

        if size > MAX_FILE_SIZE:
            logger.warning("[slack] File %s too large (%d bytes)", filename, size)
            return None

        try:
            import httpx
            token: str = await connector.get_oauth_token()
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url_private,
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=True,
                    timeout=30.0,
                )
                resp.raise_for_status()
                return resp.content, filename, content_type
        except Exception as exc:
            logger.error("[slack] Failed to download file %s: %s", filename, exc)
            return None

    def format_text(self, markdown: str) -> str:
        """Convert Markdown to Slack mrkdwn format."""
        return markdown_to_mrkdwn(markdown)

    # ------------------------------------------------------------------
    # Typing indicators (reactions)
    # ------------------------------------------------------------------

    async def add_typing_indicator(self, message: InboundMessage) -> None:
        ctx: dict[str, Any] = message.messenger_context
        workspace_id: str | None = ctx.get("workspace_id")
        channel_id: str = ctx.get("channel_id", "")
        event_ts: str = ctx.get("event_ts", message.message_id)

        if not channel_id or not event_ts:
            return

        try:
            connector = await self._get_connector(workspace_id)
            await connector.add_reaction(channel=channel_id, timestamp=event_ts)
        except Exception as exc:
            logger.debug("[slack] Failed to add reaction: %s", exc)

    async def remove_typing_indicator(self, message: InboundMessage) -> None:
        ctx: dict[str, Any] = message.messenger_context
        workspace_id: str | None = ctx.get("workspace_id")
        channel_id: str = ctx.get("channel_id", "")
        event_ts: str = ctx.get("event_ts", message.message_id)

        if not channel_id or not event_ts:
            return

        try:
            connector = await self._get_connector(workspace_id)
            await connector.remove_reaction(channel=channel_id, timestamp=event_ts)
        except Exception as exc:
            logger.debug("[slack] Failed to remove reaction: %s", exc)

    # ------------------------------------------------------------------
    # Profile helpers
    # ------------------------------------------------------------------

    def _extract_email_from_profile(self, profile: dict[str, Any]) -> str | None:
        p: dict[str, Any] = profile.get("profile", profile)
        email: str = (p.get("email") or "").strip().lower()
        return email if email else None

    # ------------------------------------------------------------------
    # Unknown user message
    # ------------------------------------------------------------------

    def unknown_user_message(self) -> str:
        return (
            "I couldn't link your Slack identity to a Basebase account. "
            "Please verify your email in Basebase or ask your admin to link your Slack user."
        )

    # ------------------------------------------------------------------
    # Channel helpers
    # ------------------------------------------------------------------

    async def _get_channel_name(
        self,
        workspace_id: str,
        channel_id: str,
        *,
        organization_id: str | None = None,
    ) -> str | None:
        """Resolve a Slack channel ID to its human-readable name with caching."""
        cache_key: tuple[str, str] = (workspace_id, channel_id)
        now: float = time.monotonic()

        async with _channel_name_cache_lock:
            cached = _channel_name_cache.get(cache_key)
            if cached and cached[1] > now:
                return cached[0]
            _channel_name_cache.pop(cache_key, None)

        name: str | None = None
        try:
            connector: SlackConnector = await self._get_connector(
                workspace_id,
                organization_id=organization_id,
            )
            channels: list[dict[str, Any]] = await connector.get_channels()
            for candidate in channels:
                if str(candidate.get("id") or "") != channel_id:
                    continue
                name = (
                    str(candidate.get("name") or "")
                    or str(candidate.get("name_normalized") or "")
                ).strip() or None
                if name:
                    break
        except Exception as exc:
            logger.debug(
                "[slack] Failed to resolve channel name ws=%s channel=%s: %s",
                workspace_id,
                channel_id,
                exc,
            )
            name = None

        ttl: int = _CHANNEL_NAME_CACHE_TTL_SECONDS if name else 120
        expiry: float = now + ttl
        async with _channel_name_cache_lock:
            if len(_channel_name_cache) >= _CHANNEL_NAME_CACHE_MAX_ENTRIES:
                expired_keys = [
                    key for key, (_, exp) in _channel_name_cache.items() if exp <= now
                ]
                for key in expired_keys:
                    _channel_name_cache.pop(key, None)
            _channel_name_cache[cache_key] = (name, expiry)

        return name

    # ------------------------------------------------------------------
    # Connector factory
    # ------------------------------------------------------------------

    async def _get_connector(
        self,
        workspace_id: str | None = None,
        *,
        organization_id: str | None = None,
    ) -> SlackConnector:
        """Instantiate a SlackConnector for the given workspace/org."""
        if organization_id:
            return SlackConnector(
                organization_id=organization_id,
                team_id=workspace_id,
            )
        if workspace_id:
            org_id: str | None = await self._resolve_org_from_workspace(workspace_id)
            if org_id:
                return SlackConnector(organization_id=org_id, team_id=workspace_id)
        raise RuntimeError(
            f"Cannot create SlackConnector: no workspace_id or organization_id"
        )
