"""
Slack messenger — platform-specific hooks for :class:`WorkspaceMessenger`.

All generic pipeline logic (user resolution, org resolution, conversation
management, streaming delivery, activity persistence) lives in
``_workspace.py``.  This file contains only the Slack-specific API calls
and formatting.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from connectors.slack import SlackConnector, markdown_to_mrkdwn
from messengers._workspace import WorkspaceMessenger
from messengers.base import InboundMessage, MessengerMeta, ResponseMode

logger = logging.getLogger(__name__)

# Tool names we do not show status for (internal/bookkeeping).
_SKIP_TOOL_STATUS: frozenset[str] = frozenset({"think", "keep_notes", "manage_memory"})

# Human-friendly status text for tool_call events. Use {connector} for connector slug.
TOOL_STATUS_LABELS: dict[str, str] = {
    "run_sql_query": "Querying your database",
    "run_sql_write": "Updating your database",
    "query_on_connector": "Looking up data in {connector}",
    "write_on_connector": "Updating records in {connector}",
    "run_on_connector": "Running action on {connector}",
    "send_slack_table": "Preparing a table",
    "trigger_sync": "Syncing data",
    "run_workflow": "Running workflow",
    "foreach": "Processing items",
    "list_connected_connectors": "Checking connected tools",
    "get_connector_docs": "Reading connector docs",
    "initiate_connector": "Setting up a connection",
}


def _tool_status_text(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Return human-friendly status text for a tool call, or None to skip posting."""
    if tool_name in _SKIP_TOOL_STATUS:
        return None
    label: str | None = TOOL_STATUS_LABELS.get(tool_name)
    if label is None:
        return None
    connector_slug: str = (tool_input.get("connector") or "").strip() if isinstance(tool_input.get("connector"), str) else ""
    if "{connector}" in label:
        connector_display: str = connector_slug.replace("_", " ").title() if connector_slug else "connector"
        return label.format(connector=connector_display)
    return label


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
        result: dict[str, Any] = await connector.post_message(
            channel=channel_id,
            text=text,
            thread_ts=thread_id,
            blocks=blocks,
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
    # Tool call status (override _handle_json_chunk)
    # ------------------------------------------------------------------

    async def _handle_json_chunk(
        self,
        chunk: str,
        channel_id: str,
        thread_id: str | None,
        workspace_id: str | None,
        organization_id: str | None,
    ) -> None:
        """Post a short status message to Slack when a tool call starts."""
        await super()._handle_json_chunk(
            chunk, channel_id, thread_id, workspace_id, organization_id,
        )
        try:
            data: dict[str, Any] = json.loads(chunk)
        except (json.JSONDecodeError, TypeError):
            return
        if data.get("type") != "tool_call":
            return
        tool_name: str = (data.get("tool_name") or "").strip()
        tool_input: dict[str, Any] = data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        status_text: str | None = _tool_status_text(tool_name, tool_input)
        if status_text is None:
            return
        message: str = f"_{status_text}…_"

        async def _post_status() -> None:
            try:
                await self.post_message(
                    channel_id=channel_id,
                    text=message,
                    thread_id=thread_id,
                    workspace_id=workspace_id,
                    organization_id=organization_id,
                )
            except Exception as exc:
                logger.debug("[slack] Tool status message failed: %s", exc)

        asyncio.create_task(_post_status())
