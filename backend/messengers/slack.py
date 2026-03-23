"""
Slack messenger — platform-specific hooks for :class:`WorkspaceMessenger`.

All generic pipeline logic (user resolution, org resolution, conversation
management, streaming delivery, activity persistence) lives in
``_workspace.py``.  This file contains only the Slack-specific API calls
and formatting.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from connectors.slack import SlackConnector, markdown_to_mrkdwn
from messengers._workspace import WorkspaceMessenger
from messengers.base import InboundMessage, MessengerMeta, ResponseMode

logger = logging.getLogger(__name__)


def _normalize_slack_dedupe_text(text: str) -> str:
    """Normalize Slack message text for duplicate detection."""
    return re.sub(r"\s+", "", text or "")


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
        """Attach human-readable channel name to messenger context."""
        ctx = message.messenger_context
        workspace_id: str | None = ctx.get("workspace_id")
        channel_id: str = ctx.get("channel_id", "")

        if not workspace_id or not channel_id:
            return

        channel_name: str | None = await self.resolve_channel_name(
            workspace_id, channel_id,
        )
        if channel_name:
            ctx.setdefault("channel_name", channel_name)

    async def post_message(
        self,
        channel_id: str,
        text: str,
        thread_id: str | None = None,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """Post a message to Slack via ``chat.postMessage``."""
        connector: SlackConnector = await self._get_connector(
            workspace_id, organization_id=organization_id,
        )
        if await self._should_skip_duplicate_thread_message(
            connector=connector,
            channel_id=channel_id,
            thread_id=thread_id,
            text=text,
        ):
            logger.info(
                "[slack] Skipping duplicate outbound message channel=%s thread_id=%s text=%s",
                channel_id,
                thread_id,
                text[:120],
            )
            return None

        result: dict[str, Any] = await connector.post_message(
            channel=channel_id,
            text=text,
            thread_ts=thread_id,
            blocks=blocks,
        )
        return result.get("ts")

    async def _should_skip_duplicate_thread_message(
        self,
        *,
        connector: SlackConnector,
        channel_id: str,
        thread_id: str | None,
        text: str,
    ) -> bool:
        """Return True when the next Slack message matches the latest thread message."""
        normalized_candidate: str = _normalize_slack_dedupe_text(text)
        if not normalized_candidate:
            return False

        try:
            if thread_id:
                messages = await connector.get_thread_messages(
                    channel_id=channel_id,
                    thread_ts=thread_id,
                    limit=1000,
                )
                latest_message = next(
                    (
                        msg for msg in reversed(messages)
                        if (msg.get("text") or "").strip()
                    ),
                    None,
                )
            else:
                messages = await connector.get_channel_messages(
                    channel_id,
                    limit=1,
                )
                latest_message = next(
                    (msg for msg in messages if (msg.get("text") or "").strip()),
                    None,
                )
        except Exception as exc:
            logger.debug(
                "[slack] Duplicate message check failed channel=%s thread_id=%s: %s",
                channel_id,
                thread_id,
                exc,
            )
            return False

        if latest_message is None:
            return False

        latest_text: str = latest_message.get("text") or ""
        normalized_latest: str = _normalize_slack_dedupe_text(latest_text)
        is_duplicate: bool = normalized_latest == normalized_candidate
        logger.debug(
            "[slack] Duplicate message check channel=%s thread_id=%s duplicate=%s latest_ts=%s",
            channel_id,
            thread_id,
            is_duplicate,
            latest_message.get("ts"),
        )
        return is_duplicate

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
            token, _connection_id = await connector.get_oauth_token()
            auth_headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Don't auto-follow redirects — httpx strips the Authorization
                # header on cross-origin redirects (e.g. files.slack.com →
                # basebase-ai.slack.com), which returns an HTML login page
                # instead of the actual file.
                resp = await client.get(
                    url_private,
                    headers=auth_headers,
                    follow_redirects=False,
                )
                redirects_followed: int = 0
                while resp.is_redirect and redirects_followed < 5:
                    redirect_url: str | None = resp.headers.get("location")
                    if not redirect_url:
                        break
                    resp = await client.get(
                        redirect_url,
                        headers=auth_headers,
                        follow_redirects=False,
                    )
                    redirects_followed += 1
                resp.raise_for_status()

                # Guard against HTML login pages returned on auth failure
                resp_ct: str = resp.headers.get("content-type", "")
                if "text/html" in resp_ct and not content_type.startswith("text/"):
                    logger.error(
                        "[slack] File download returned HTML instead of %s for %s",
                        content_type, filename,
                    )
                    return None

                return resp.content, filename, content_type
        except Exception as exc:
            logger.error("[slack] Failed to download file %s: %s", filename, exc)
            return None

    def format_text(self, markdown: str) -> str:
        """Convert Markdown to Slack mrkdwn format."""
        text, _ = markdown_to_mrkdwn(markdown)
        return text

    async def format_and_post(
        self,
        channel_id: str,
        thread_id: str | None,
        text_to_send: str,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        """Format with markdown_to_mrkdwn and post; use blocks when table is present."""
        text: str
        blocks: list[dict[str, Any]] | None
        text, blocks = markdown_to_mrkdwn(text_to_send)
        await self.post_message(
            channel_id=channel_id,
            text=text,
            thread_id=thread_id,
            workspace_id=workspace_id,
            organization_id=organization_id,
            blocks=blocks,
        )

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

    async def fetch_channel_name(
        self,
        workspace_id: str,
        channel_id: str,
    ) -> str | None:
        """Fetch channel name from Slack via ``conversations.info``."""
        try:
            connector: SlackConnector = await self._get_connector(workspace_id)
            info: dict[str, Any] | None = await connector.get_channel_info(channel_id)
            if info:
                return info.get("name") or info.get("name_normalized")
        except Exception as exc:
            logger.debug("[slack] Failed to fetch channel name for %s: %s", channel_id, exc)
        return None

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

    # ------------------------------------------------------------------
    # Tool call status (format only; base class posts using status_text from stream)
    # ------------------------------------------------------------------

    def format_tool_status_for_display(self, status_text: str) -> str:
        """Slack mrkdwn italic for tool status messages."""
        return f"_{status_text}…_"
