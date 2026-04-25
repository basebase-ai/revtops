"""
Slack connector implementation.

Responsibilities:
- Authenticate with Slack using OAuth token
- Periodic sync: join public channels so the Events API delivers messages
- On-demand: fetch_channel_history for a single channel (agent/tool use)
- Normalize Slack payloads to activity-shaped dicts when fetching history
- Handle pagination and rate limits
"""

import asyncio
import base64
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select


_SEPARATOR_ROW_RE: re.Pattern[str] = re.compile(
    r'^\|?[\s\-:|]+\|?$'
)
_SLACK_MESSAGE_PERMALINK_RE: re.Pattern[str] = re.compile(
    r"^/archives/(?P<channel>[A-Z0-9]+)/p(?P<raw_ts>\d{16,})$",
    re.IGNORECASE,
)
_MARKDOWN_TABLE_CODE_BLOCK_TEMPLATE: str = "```\n{table}\n```"
_markdown_logger = logging.getLogger(__name__)


def _clean_table_lines(raw: str) -> str:
    """Strip markdown separator rows from a pipe-delimited table."""
    lines: list[str] = raw.strip().split('\n')
    filtered: list[str] = [
        line for line in lines if not _SEPARATOR_ROW_RE.match(line.strip())
    ]
    return '\n'.join(filtered)


def markdown_to_mrkdwn(text: str) -> tuple[str, Optional[list[dict[str, Any]]]]:
    """Convert standard Markdown to Slack mrkdwn format.

    Handles bold, italic, links, headers, and tables.  Existing fenced code
    blocks are extracted first. Tables may be returned as Block Kit blocks.
    Returns (mrkdwn_text, blocks_or_none).
    """
    from connectors.slack_tables import format_markdown_table_inline

    # Placeholder content: plain string, or table metadata for later rendering.
    code_blocks: list[str | dict[str, Any]] = []
    _FENCE_RE: re.Pattern[str] = re.compile(r'```\w*\n(.*?)```', re.DOTALL)

    def _extract_fence(match: re.Match[str]) -> str:
        content: str = match.group(1)
        idx: int = len(code_blocks)
        if '|' in content:
            table_blocks, fallback = format_markdown_table_inline(content)
            code_blocks.append({
                "type": "table",
                "table_blocks": table_blocks,
                "fallback": fallback,
                "raw_table": content.strip(),
            })
        else:
            code_blocks.append('```\n' + content.strip() + '\n```')
        return f'\x00CB{idx}\x00'

    text = _FENCE_RE.sub(_extract_fence, text)

    # -- Step 2: wrap bare markdown tables that weren't already fenced ------
    _TABLE_RE: re.Pattern[str] = re.compile(
        r'((?:^(?:\|.+\||[^\n|]+(?:\|[^\n|]+){2,})$\n?)+)',
        re.MULTILINE,
    )

    def _wrap_table(match: re.Match[str]) -> str:
        cleaned: str = _clean_table_lines(match.group(1))
        table_blocks, fallback = format_markdown_table_inline(cleaned)
        idx = len(code_blocks)
        code_blocks.append({
            "type": "table",
            "table_blocks": table_blocks,
            "fallback": fallback,
            "raw_table": cleaned.strip(),
        })
        return f'\x00CB{idx}\x00'

    text = _TABLE_RE.sub(_wrap_table, text)

    # -- Step 3: inline formatting ------------------------------------------
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)

    # -- Step 4: restore placeholders and decide whether to use table blocks --
    table_block_count: int = 0
    for block in code_blocks:
        if isinstance(block, dict) and block.get("type") == "table" and block.get("table_blocks") is not None:
            table_block_count += 1

    use_table_blocks: bool = table_block_count == 1
    if table_block_count > 1:
        _markdown_logger.info(
            "[SlackConnector] Detected %d markdown tables with block representation; "
            "falling back to inline code blocks so all tables render in one Slack message.",
            table_block_count,
        )

    out_blocks: Optional[list[dict[str, Any]]] = None
    for i, block in enumerate(code_blocks):
        if isinstance(block, dict) and block.get("type") == "table":
            table_blocks = block.get("table_blocks")
            fallback = str(block.get("fallback") or "")
            raw_table = str(block.get("raw_table") or "").strip()

            if use_table_blocks and table_blocks is not None and out_blocks is None:
                text = text.replace(f'\x00CB{i}\x00', fallback)
                out_blocks = table_blocks
            else:
                code_fallback: str = (
                    _MARKDOWN_TABLE_CODE_BLOCK_TEMPLATE.format(table=raw_table)
                    if raw_table else fallback
                )
                text = text.replace(f'\x00CB{i}\x00', code_fallback)
        else:
            assert isinstance(block, str)
            text = text.replace(f'\x00CB{i}\x00', block)

    return (text, out_blocks)


def _extract_fallback_text_from_blocks(blocks: list[dict[str, Any]]) -> str:
    """Best-effort plain-text fallback extracted from Slack blocks."""

    max_length: int = 280
    fragments: list[str] = []

    def _walk(node: Any) -> None:
        if len(" ".join(fragments)) >= max_length:
            return
        if isinstance(node, dict):
            text_value = node.get("text")
            if isinstance(text_value, str) and text_value.strip():
                fragments.append(text_value.strip())
            for key in ("elements", "fields", "accessory", "title"):
                child = node.get(key)
                if child is not None:
                    _walk(child)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    _walk(value)
            return
        if isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(blocks)
    fallback: str = " ".join(fragment for fragment in fragments if fragment).strip()
    if len(fallback) > max_length:
        fallback = f"{fallback[: max_length - 3].rstrip()}..."
    return fallback

from api.websockets import broadcast_sync_progress
from connectors.base import BaseConnector, ExternalConnectionRevokedError, build_connection_removed_message
from connectors.registry import (
    AuthType, Capability, ConnectorAction, ConnectorMeta, ConnectorScope,
)
from models.activity import Activity
from models.database import get_session
from models.integration import Integration
from models.user import User

SLACK_API_BASE = "https://slack.com/api"
logger = logging.getLogger(__name__)


def _parse_since_iso_to_utc_timestamp(raw: str) -> float:
    """Parse an ISO 8601 datetime string to a UTC Unix timestamp for Slack ``oldest``."""
    s: str = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt: datetime = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.timestamp()


class SlackConnector(BaseConnector):
    """Connector for Slack workspace data."""

    source_system = "slack"
    meta = ConnectorMeta(
        name="Slack",
        slug="slack",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["activities"],
        capabilities=[Capability.SYNC, Capability.QUERY, Capability.ACTION, Capability.LISTEN],
        actions=[
            ConnectorAction(
                name="send_message",
                description="Send a message to Slack. Provide `channel` for channels/DM IDs, or `user_id` to open a DM and send directly to that user. Uses Slack mrkdwn: *bold*, _italic_, ~strike~.",
                parameters=[
                    {"name": "channel", "type": "string", "required": False, "description": "Channel/DM/MPIM ID (e.g. 'C123', 'D123', 'G123') or channel name"},
                    {"name": "user_id", "type": "string", "required": False, "description": "Slack user ID (e.g. 'U123'). If provided without channel, Basebase opens a DM to this user and sends the message."},
                    {"name": "text", "type": "string", "required": True, "description": "Message text in Slack mrkdwn format"},
                    {"name": "thread_ts", "type": "string", "required": False, "description": "Thread timestamp to reply in-thread (when channel is provided)"},
                ],
            ),
            ConnectorAction(
                name="fetch_channel_history",
                description="Fetch message history for a single channel since a datetime (on-demand; periodic sync does not backfill). Uses conversations.history with rate limiting.",
                parameters=[
                    {"name": "channel", "type": "string", "required": True, "description": "Channel ID or name (e.g. '#general' or 'C123')"},
                    {"name": "since", "type": "string", "required": True, "description": "ISO 8601 datetime; only messages after this time are returned"},
                    {"name": "limit", "type": "integer", "required": False, "description": "Max messages to return (default 1000, max 5000)"},
                ],
            ),
        ],
        nango_integration_id="slack",
        description="Slack workspace – messages, channels, and real-time events",
        usage_guide="""# Slack Usage Guide

## Query: list_channels

Call via `query_on_connector(connector='slack', query='list_channels')`.

Returns all channels the bot can see, with id, name, and is_private.

## Query: channel_info

Call via `query_on_connector(connector='slack', query='channel_info:<channel_id>')`.

Returns details for a single channel including topic and purpose.

## Query: read_file

Call via `query_on_connector(connector='slack', query='read_file:<file_id_or_url>')`.

Reads a Slack file by file ID (recommended) or a `url_private` URL and returns
metadata + extracted text for text/PDF files. Binary files return base64.

## Action: send_message

Call via `run_on_connector(connector='slack', action='send_message', params={...})`.

Send a message to a Slack channel, DM, or user.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| channel | string | No* | Channel ID (C123), DM ID (D123), or MPIM ID (G123). Use for channels or existing DMs. |
| user_id | string | No* | Slack user ID (U123). If provided without channel, opens a DM to this user and sends. |
| text | string | Yes | Message content in Slack mrkdwn format |
| thread_ts | string | No | Thread timestamp to reply in-thread (only when channel is provided) |

*Provide either `channel` or `user_id` (or both — channel takes precedence).

### Slack mrkdwn format

- **Bold**: `*text*`
- **Italic**: `_text_`
- **Strikethrough**: `~text~`
- **Links**: `<https://example.com|link text>`
- **Code**: `` `code` `` (inline) or ``` ```code block``` ``` (multiline)
- **Headers**: Use `*Header*` for emphasis (Slack has no native headers)

**Note:** Standard Markdown (`**bold**`, `[text](url)`) is automatically converted to mrkdwn when possible.

### Examples

**Send to a channel:**
```json
{"channel": "C01234ABCD", "text": "*Reminder:* Standup in 5 minutes!"}
```

**Send to a user (opens DM):**
```json
{"user_id": "U01234ABCD", "text": "Here's the report you asked for."}
```

**Reply in a thread:**
```json
{"channel": "C01234ABCD", "text": "Got it, will follow up.", "thread_ts": "1234567890.123456"}
```

## Action: fetch_channel_history

Call via `run_on_connector(connector='slack', action='fetch_channel_history', params={...})`.

Returns normalized messages for one channel since a cutoff (does not write to the DB).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| channel | string | Yes | Channel ID or `#name` |
| since | string | Yes | ISO 8601 datetime (e.g. `2025-03-01T00:00:00Z`) |
| limit | integer | No | Max messages (default 1000, max 5000) |
""",
    )

    def __init__(
        self,
        organization_id: str,
        user_id: str | None = None,
        team_id: str | None = None,
        *,
        sync_since_override: datetime | None = None,
    ) -> None:
        """Initialize Slack connector.

        Args:
            organization_id: Organization UUID.
            user_id: Optional owner user UUID.
            team_id: Optional Slack team/workspace ID used to disambiguate
                between multiple Slack integrations in the same org.
            sync_since_override: Optional manual resync-from cutoff (naive UTC).
        """
        super().__init__(
            organization_id=organization_id,
            user_id=user_id,
            sync_since_override=sync_since_override,
        )
        self.team_id = (team_id or "").strip() or None
        self._user_info_cache: dict[str, dict[str, Any]] = {}

    async def _select_integration(
        self,
        session: Any,
        *,
        require_active: bool = False,
    ) -> Integration | None:
        """Select the matching Slack integration.

        When team_id is provided, prefer an integration with matching
        ``extra_data.team_id`` to avoid cross-workspace token mix-ups.
        """
        if not self.team_id:
            return await super()._select_integration(
                session,
                require_active=require_active,
            )

        conditions = [
            Integration.organization_id == uuid.UUID(self.organization_id),
            Integration.connector == self.source_system,
            Integration.extra_data["team_id"].astext == self.team_id,
        ]
        if require_active:
            conditions.append(Integration.is_active == True)  # noqa: E712
        if self.user_id:
            conditions.append(Integration.user_id == uuid.UUID(self.user_id))

        result = await session.execute(
            select(Integration)
            .where(*conditions)
            .order_by(
                Integration.updated_at.desc().nullslast(),
                Integration.created_at.desc().nullslast(),
            )
            .limit(2)
        )
        candidates = result.scalars().all()
        if len(candidates) > 1 and not self.user_id:
            logger.warning(
                "Multiple Slack integrations matched org=%s team=%s with no user_id; using integration=%s",
                self.organization_id,
                self.team_id,
                candidates[0].id,
            )
        if candidates:
            return candidates[0]

        logger.warning(
            "No Slack integration matched org=%s team=%s; falling back to default connector selection",
            self.organization_id,
            self.team_id,
        )
        return await super()._select_integration(
            session,
            require_active=require_active,
        )

    async def get_oauth_token(self) -> tuple[str, str]:
        """
        Get Slack token: prefer bot-install token for this team_id, else Nango.
        """
        if self._token:
            return self._token, ""

        # When we have team_id, try Add-to-Slack (bot install) token first
        if self.team_id:
            from services.slack_bot_install import get_slack_bot_token

            bot_token: str | None = await get_slack_bot_token(
                self.organization_id, self.team_id
            )
            if bot_token:
                self._token = bot_token
                return self._token, ""

        # If team_id was not provided at initialization, infer it from the
        # selected integration so action calls can still use Add-to-Slack
        # bot tokens that include chat:write scope.
        if not self.team_id:
            if not self._integration:
                await self._load_integration()
            inferred_team_id = (
                (self._integration.extra_data or {}).get("team_id")
                if self._integration
                else None
            )
            inferred_team_id = str(inferred_team_id or "").strip() or None
            if inferred_team_id:
                from services.slack_bot_install import get_slack_bot_token

                bot_token = await get_slack_bot_token(
                    self.organization_id,
                    inferred_team_id,
                )
                if bot_token:
                    logger.info(
                        "[SlackConnector] Using bot-install token inferred from integration team_id=%s",
                        inferred_team_id,
                    )
                    self.team_id = inferred_team_id
                    self._token = bot_token
                    return self._token, ""

        return await super().get_oauth_token()

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Slack API."""
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    _MAX_RETRIES: int = 5
    _MIN_REQUEST_INTERVAL: float = 1.5  # seconds between Slack API calls (Tier 3 ≈ 50/min)
    _last_request_time: float = 0.0

    async def _throttle(self) -> None:
        """Pre-emptive throttle to stay under Slack's per-minute rate limit."""
        import time
        now: float = time.monotonic()
        elapsed: float = now - self._last_request_time
        if elapsed < self._MIN_REQUEST_INTERVAL:
            await asyncio.sleep(self._MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Slack API with rate-limit retry."""
        await self._throttle()
        headers: dict[str, str] = await self._get_headers()
        url: str = f"{SLACK_API_BASE}/{endpoint}"

        for attempt in range(self._MAX_RETRIES + 1):
            async with httpx.AsyncClient() as client:
                if method == "GET":
                    response: httpx.Response = await client.get(
                        url, headers=headers, params=params, timeout=30.0
                    )
                else:
                    response = await client.post(
                        url, headers=headers, json=json_data, timeout=30.0
                    )

                if response.status_code == 429 and attempt < self._MAX_RETRIES:
                    retry_after: float = float(
                        response.headers.get("Retry-After", str(2 ** attempt))
                    )
                    wait_time: float = retry_after + 10.0
                    logger.warning(
                        "[Slack API] 429 rate-limited on %s (attempt %d/%d), retrying in %.1fs (Retry-After: %.0fs + 10s buffer)",
                        endpoint,
                        attempt + 1,
                        self._MAX_RETRIES,
                        wait_time,
                        retry_after,
                    )
                    await asyncio.sleep(wait_time)
                    continue

                if response.status_code >= 500 and attempt < self._MAX_RETRIES:
                    backoff: float = float(2 ** attempt)
                    logger.warning(
                        "[Slack API] %d server error on %s (attempt %d/%d), retrying in %.1fs",
                        response.status_code,
                        endpoint,
                        attempt + 1,
                        self._MAX_RETRIES,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue

                response.raise_for_status()
                data: dict[str, Any] = response.json()

                if not data.get("ok"):
                    error_code = str(data.get("error", "Unknown"))
                    if error_code in {"invalid_auth", "account_inactive", "token_revoked", "not_authed"}:
                        logger.warning(
                            "[Slack API] Slack connection was revoked org=%s user=%s endpoint=%s error=%s",
                            self.organization_id,
                            self.user_id,
                            endpoint,
                            error_code,
                        )
                        raise ExternalConnectionRevokedError(
                            build_connection_removed_message(self.source_system)
                        )
                    raise ValueError(f"Slack API error: {error_code}")

                return data

        raise httpx.HTTPStatusError(
            f"Slack API request failed after {self._MAX_RETRIES} retries (last status {response.status_code})",
            request=response.request,
            response=response,
        )

    async def get_channels(self) -> list[dict[str, Any]]:
        """Get list of channels the bot has access to."""
        channels: list[dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            params: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._make_request("GET", "conversations.list", params=params)
            channels.extend(data.get("channels", []))

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return channels

    async def get_channel_info(self, channel_id: str) -> dict[str, Any] | None:
        """Fetch channel metadata via ``conversations.info``. Returns the channel dict or None."""
        try:
            data = await self._make_request(
                "GET", "conversations.info", params={"channel": channel_id}
            )
            return data.get("channel")
        except Exception as exc:
            logger.debug(
                "[Slack] conversations.info failed for channel=%s: %s",
                channel_id, exc,
            )
            return None

    async def join_channel(self, channel_id: str) -> bool:
        """Join a public channel. Returns True if joined or already a member. Requires channels:join scope."""
        try:
            data = await self._make_request(
                "POST", "conversations.join", json_data={"channel": channel_id}
            )
            return bool(data.get("ok"))
        except Exception as exc:
            logger.debug(
                "[Slack Sync] conversations.join failed for channel=%s: %s",
                channel_id,
                exc,
            )
            return False

    async def get_channel_messages(
        self,
        channel_id: str,
        oldest: Optional[float] = None,
        limit: int = 100,
        latest: Optional[str] = None,
        inclusive: bool = False,
    ) -> list[dict[str, Any]]:
        """Get messages from a specific channel."""
        messages: list[dict[str, Any]] = []
        cursor: Optional[str] = None

        while len(messages) < limit:
            params: dict[str, Any] = {
                "channel": channel_id,
                "limit": min(100, limit - len(messages)),
            }
            if oldest:
                params["oldest"] = oldest
            if latest:
                params["latest"] = latest
                params["inclusive"] = inclusive
            if cursor:
                params["cursor"] = cursor

            data = await self._make_request(
                "GET", "conversations.history", params=params
            )
            messages.extend(data.get("messages", []))

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return messages

    async def get_thread_messages(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Get messages from a specific Slack thread."""
        messages: list[dict[str, Any]] = []
        cursor: Optional[str] = None

        while len(messages) < limit:
            params: dict[str, Any] = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": min(100, limit - len(messages)),
                "inclusive": True,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._make_request(
                "GET", "conversations.replies", params=params
            )
            messages.extend(data.get("messages", []))

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return messages

    async def fetch_channel_history(
        self,
        channel: str,
        since: str,
        *,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Fetch normalized message history for one channel since an ISO 8601 cutoff.

        Does not persist to the database; use for on-demand/agent backfill. Respects
        Slack rate limits via :meth:`_make_request`. Joins the channel if needed
        (public channels only).
        """
        channel_stripped: str = channel.strip()
        since_stripped: str = since.strip()
        if not channel_stripped:
            raise ValueError("fetch_channel_history requires a non-empty channel")
        if not since_stripped:
            raise ValueError("fetch_channel_history requires a non-empty since (ISO 8601 datetime)")

        oldest_ts: float = _parse_since_iso_to_utc_timestamp(since_stripped)
        channel_id: str = await self._resolve_channel_for_post(channel_stripped)
        info: dict[str, Any] | None = await self.get_channel_info(channel_id)
        channel_name: str = str((info or {}).get("name") or channel_stripped).lstrip("#")

        cap: int = max(1, min(limit, 5000))

        try:
            messages: list[dict[str, Any]] = await self.get_channel_messages(
                channel_id, oldest=oldest_ts, limit=cap
            )
        except ValueError as exc:
            if "not_in_channel" not in str(exc):
                raise
            await self.join_channel(channel_id)
            messages = await self.get_channel_messages(
                channel_id, oldest=oldest_ts, limit=cap
            )

        rows: list[dict[str, Any]] = []
        for msg in messages:
            uid_slack: Any = msg.get("user")
            if isinstance(uid_slack, str) and uid_slack and not msg.get("user_profile"):
                if uid_slack not in self._user_info_cache:
                    try:
                        self._user_info_cache[uid_slack] = await self.get_user_info(uid_slack)
                    except Exception:
                        self._user_info_cache[uid_slack] = {}
                profile: dict[str, Any] = (
                    (self._user_info_cache.get(uid_slack) or {}).get("profile") or {}
                )
                if profile:
                    msg["user_profile"] = profile

            activity: Activity | None = self._normalize_message(msg, channel_id, channel_name)
            if activity is None:
                continue
            rows.append(
                {
                    "source_id": activity.source_id,
                    "type": activity.type,
                    "subject": activity.subject,
                    "description": activity.description,
                    "activity_date": activity.activity_date.isoformat()
                    if activity.activity_date
                    else None,
                    "custom_fields": activity.custom_fields,
                }
            )

        return {
            "ok": True,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "count": len(rows),
            "messages": rows,
        }

    async def get_users(self) -> list[dict[str, Any]]:
        """Get list of workspace users."""
        users: list[dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor

            data = await self._make_request("GET", "users.list", params=params)
            users.extend(data.get("members", []))

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return users

    async def list_external_users(self) -> list[dict[str, Any]]:
        """
        List real workspace members for identity mapping wizard.

        Filters out bots, app users, and deactivated accounts.
        """
        raw_users: list[dict[str, Any]] = await self.get_users()
        result: list[dict[str, Any]] = []
        for u in raw_users:
            if u.get("is_bot") or u.get("id") == "USLACKBOT":
                continue
            if u.get("deleted"):
                continue
            profile: dict[str, Any] = u.get("profile", {})
            display_name: str = (
                profile.get("real_name")
                or profile.get("display_name")
                or u.get("name", "")
            )
            if not display_name:
                continue
            result.append({
                "external_id": u["id"],
                "display_name": display_name,
                "email": profile.get("email"),
                "avatar_url": profile.get("image_72") or profile.get("image_48"),
                "source": "slack",
            })
        return result

    async def get_user_info(self, slack_user_id: str) -> dict[str, Any]:
        """Get details for a specific Slack user via users.info."""
        data = await self._make_request(
            "GET",
            "users.info",
            params={"user": slack_user_id},
        )
        return data.get("user", {})

    async def _fetch_current_user_id_for_mapping(self) -> Optional[str]:
        """Fetch the current RevTops user ID for Slack mapping."""
        if not self._integration:
            logger.warning(
                "[Slack Sync] Missing integration context when fetching current user id for org=%s",
                self.organization_id,
            )
            return None

        candidate_ids: list[str] = []
        if self.user_id:
            candidate_ids.append(self.user_id)
        if self._integration.user_id:
            candidate_ids.append(str(self._integration.user_id))
        if self._integration.connected_by_user_id:
            candidate_ids.append(str(self._integration.connected_by_user_id))

        deduped_candidates = list(dict.fromkeys(candidate_ids))
        logger.info(
            "[Slack Sync] Candidate RevTops user IDs for mapping org=%s integration=%s candidates=%s",
            self.organization_id,
            self._integration.id,
            deduped_candidates,
        )

        if not deduped_candidates:
            logger.warning(
                "[Slack Sync] No RevTops user ID candidates found for org=%s integration=%s",
                self.organization_id,
                self._integration.id,
            )
            return None

        preferred_id = deduped_candidates[0]
        try:
            user_uuid = uuid.UUID(preferred_id)
        except ValueError:
            logger.warning(
                "[Slack Sync] Invalid RevTops user ID candidate %s for org=%s integration=%s",
                preferred_id,
                self.organization_id,
                self._integration.id,
            )
            return None

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(select(User).where(User.id == user_uuid))
            user = result.scalar_one_or_none()

        if not user:
            logger.warning(
                "[Slack Sync] RevTops user %s not found for org=%s integration=%s",
                preferred_id,
                self.organization_id,
                self._integration.id,
            )
            return None

        if self.user_id != str(user.id):
            logger.info(
                "[Slack Sync] Updating connector user_id from %s to %s for org=%s integration=%s",
                self.user_id,
                user.id,
                self.organization_id,
                self._integration.id,
            )
            self.user_id = str(user.id)

        logger.info(
            "[Slack Sync] Resolved current RevTops user id=%s email=%s org=%s integration=%s",
            user.id,
            user.email,
            self.organization_id,
            self._integration.id,
        )
        return str(user.id)

    async def sync_deals(self) -> int:
        """Slack doesn't have deals - return 0."""
        return 0

    async def sync_accounts(self) -> int:
        """Slack doesn't have accounts - return 0."""
        return 0

    async def sync_contacts(self) -> int:
        """Slack doesn't have contacts in the traditional sense - return 0."""
        return 0

    async def ensure_channel_membership(self) -> dict[str, int]:
        """List workspace channels and join public channels the bot is not yet in.

        Ongoing message ingestion uses the Slack Events API webhook; periodic sync
        only ensures the bot is a member of public channels so events are delivered.
        Private channels cannot be self-joined and require a manual invite.

        Returns:
            Counts: joined, already_member, skipped_private, skipped_archived, total_listed.
        """
        logger.info(
            "[Slack Sync] Starting channel membership pass for org=%s",
            self.organization_id,
        )
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
        )

        all_channels: list[dict[str, Any]] = await self.get_channels()
        joined: int = 0
        already_member: int = 0
        skipped_private: int = 0
        skipped_archived: int = 0

        for ch in all_channels:
            if ch.get("is_archived"):
                skipped_archived += 1
                continue
            if ch.get("is_private"):
                skipped_private += 1
                continue
            cid: str = str(ch.get("id") or "").strip()
            if not cid:
                continue
            if ch.get("is_member"):
                already_member += 1
                continue
            if await self.join_channel(cid):
                joined += 1
                logger.info(
                    "[Slack Sync] Joined public channel id=%s name=%s org=%s",
                    cid,
                    ch.get("name", ""),
                    self.organization_id,
                )
            else:
                logger.warning(
                    "[Slack Sync] conversations.join did not succeed for id=%s name=%s org=%s",
                    cid,
                    ch.get("name", ""),
                    self.organization_id,
                )

        logger.info(
            "[Slack Sync] Channel membership: joined=%d already_member=%d "
            "skipped_private=%d skipped_archived=%d total_listed=%d org=%s",
            joined,
            already_member,
            skipped_private,
            skipped_archived,
            len(all_channels),
            self.organization_id,
        )
        return {
            "joined": joined,
            "already_member": already_member,
            "skipped_private": skipped_private,
            "skipped_archived": skipped_archived,
            "total_listed": len(all_channels),
        }

    async def sync_activities(self) -> int:
        """Satisfy BaseConnector: periodic Slack work is channel membership only."""
        stats: dict[str, int] = await self.ensure_channel_membership()
        return int(stats.get("joined", 0))

    def _extract_sender_fields(self, slack_msg: dict[str, Any]) -> dict[str, Any]:
        user_id = slack_msg.get("user")
        bot_id = slack_msg.get("bot_id")
        user_profile = slack_msg.get("user_profile") or {}
        sender_name = (
            user_profile.get("display_name")
            or user_profile.get("real_name")
            or slack_msg.get("username")
            or ""
        ).strip()
        sender_real_name = (user_profile.get("real_name") or "").strip()
        sender_email = (user_profile.get("email") or "").strip()
        sender_fields: dict[str, Any] = {
            "sender_id": user_id or bot_id,
            "sender_type": "user" if user_id else ("bot" if bot_id else "unknown"),
            "sender_name": sender_name or None,
            "sender_real_name": sender_real_name or None,
            "sender_email": sender_email or None,
        }
        if not sender_fields["sender_id"]:
            logger.debug(
                "[Slack Sync] Missing sender id in message ts=%s channel=%s",
                slack_msg.get("ts"),
                slack_msg.get("channel"),
            )
        elif not sender_fields["sender_name"] and sender_fields["sender_type"] == "user":
            logger.debug(
                "[Slack Sync] Missing sender name in message ts=%s channel=%s user=%s",
                slack_msg.get("ts"),
                slack_msg.get("channel"),
                sender_fields["sender_id"],
            )
        return sender_fields

    def _normalize_message(
        self,
        slack_msg: dict[str, Any],
        channel_id: str,
        channel_name: str,
    ) -> Optional[Activity]:
        """Transform Slack message to our Activity model."""
        # Skip bot messages and system messages
        if slack_msg.get("subtype") in ["bot_message", "channel_join", "channel_leave"]:
            return None

        msg_ts = slack_msg.get("ts", "")
        text = slack_msg.get("text", "")

        # Skip empty messages
        if not text.strip():
            return None

        # Parse timestamp
        activity_date: Optional[datetime] = None
        if msg_ts:
            try:
                activity_date = datetime.fromtimestamp(float(msg_ts))
            except (ValueError, TypeError):
                pass

        # Create a unique source ID from channel and timestamp
        source_id = f"{channel_id}:{msg_ts}"

        sender_fields = self._extract_sender_fields(slack_msg)
        vis: dict[str, Any] = self._activity_visibility_fields()
        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=source_id,
            type="slack_message",
            subject=f"#{channel_name}",
            description=text[:1000],  # Truncate very long messages
            activity_date=activity_date,
            **vis,
            custom_fields={
                "channel_id": channel_id,
                "channel_name": channel_name,
                "user_id": slack_msg.get("user"),
                **sender_fields,
                "thread_ts": slack_msg.get("thread_ts"),
                "has_attachments": len(slack_msg.get("attachments", [])) > 0,
                "has_files": len(slack_msg.get("files", [])) > 0,
            },
        )

    async def _count_slack_activity_stats(self) -> tuple[int, int]:
        """Return (message_count, distinct_channel_count) for Slack activities in this org.

        Uses admin session so counts reflect all rows in the database (webhook + any
        historical sync), not RLS-filtered subsets.
        """
        from models.activity import Activity as ActivityModel
        from models.database import get_admin_session

        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)
        channel_id_text = ActivityModel.custom_fields["channel_id"].astext

        async with get_admin_session() as session:
            result = await session.execute(
                select(
                    func.count(ActivityModel.id),
                    func.count(func.distinct(channel_id_text)),
                ).where(
                    ActivityModel.organization_id == org_uuid,
                    ActivityModel.source_system == "slack",
                )
            )
            row = result.one()
            message_count: int = int(row[0] or 0)
            distinct_channels: int = int(row[1] or 0)
        return message_count, distinct_channels

    async def sync_all(self) -> dict[str, int]:
        """Run all sync operations."""
        from services.slack_identity import (
            refresh_slack_user_mappings_from_directory,
            refresh_slack_user_mappings_for_org,
            upsert_slack_user_mapping_from_nango_action,
        )
        from services.nango import get_nango_client
        from config import get_nango_integration_id

        await self.ensure_sync_active("sync_all:start")

        try:
            current_user_id = await self._fetch_current_user_id_for_mapping()
            if not self._integration or not self._integration.nango_connection_id:
                logger.warning(
                    "[Slack Sync] Missing Nango connection for org=%s when fetching Slack user info",
                    self.organization_id,
                )
            else:
                logger.info(
                    "[Slack Sync] Executing Nango get-user-info action for org=%s integration=%s",
                    self.organization_id,
                    self._integration.id,
                )
                nango = get_nango_client()
                action_response = await nango.execute_action(
                    integration_id=get_nango_integration_id(self.source_system),
                    connection_id=self._integration.nango_connection_id,
                    action_name="get-user-info",
                    input_payload={},
                )
                slack_user_payload: dict[str, Any] | None = None
                if isinstance(action_response, dict):
                    slack_user_payload = (
                        action_response.get("user")
                        or action_response.get("data", {}).get("user")
                        or action_response.get("data")
                        or action_response.get("result")
                    )
                logger.info(
                    "[Slack Sync] Nango get-user-info response org=%s integration=%s payload_keys=%s",
                    self.organization_id,
                    self._integration.id,
                    sorted(action_response.keys()) if isinstance(action_response, dict) else "n/a",
                )
                if not slack_user_payload or not isinstance(slack_user_payload, dict):
                    logger.warning(
                        "[Slack Sync] Nango get-user-info payload not usable org=%s integration=%s payload_type=%s",
                        self.organization_id,
                        self._integration.id,
                        type(slack_user_payload).__name__ if slack_user_payload is not None else "none",
                    )
                elif not current_user_id:
                    logger.warning(
                        "[Slack Sync] Missing current RevTops user id for mapping org=%s integration=%s",
                        self.organization_id,
                        self._integration.id,
                    )
                else:
                    logger.info(
                        "[Slack Sync] Upserting Slack mapping from Nango action org=%s integration=%s user_id=%s payload_keys=%s",
                        self.organization_id,
                        self._integration.id,
                        current_user_id,
                        sorted(slack_user_payload.keys()),
                    )
                    await upsert_slack_user_mapping_from_nango_action(
                        organization_id=self.organization_id,
                        user_id=uuid.UUID(current_user_id),
                        slack_user_payload=slack_user_payload,
                    )
        except Exception as exc:
            logger.warning(
                "[Slack Sync] Failed to map Slack user via Nango action for org=%s: %s",
                self.organization_id,
                exc,
                exc_info=True,
            )

        try:
            if self.sync_since:
                logger.info(
                    "[Slack Sync] Incremental sync — skipping full directory mapping refresh for org=%s",
                    self.organization_id,
                )
            else:
                logger.info(
                    "[Slack Sync] First sync — refreshing Slack directory user mappings for org=%s",
                    self.organization_id,
                )
                directory_count = await refresh_slack_user_mappings_from_directory(
                    organization_id=self.organization_id,
                    connector=self,
                )
                logger.info(
                    "[Slack Sync] Refreshed %d Slack directory user mappings for org=%s",
                    directory_count,
                    self.organization_id,
                )
                refreshed_count = await refresh_slack_user_mappings_for_org(self.organization_id)
                logger.info(
                    "[Slack Sync] Refreshed %d Slack user mappings for org=%s",
                    refreshed_count,
                    self.organization_id,
                )
        except Exception as exc:
            logger.warning(
                "[Slack Sync] Failed to refresh Slack user mappings for org=%s: %s",
                self.organization_id,
                exc,
                exc_info=True,
            )

        membership: dict[str, int] = await self.ensure_channel_membership()
        joined_count: int = int(membership.get("joined", 0))
        already_member_count: int = int(membership.get("already_member", 0))
        channels_joined: int = already_member_count + joined_count

        activity_count: int = 0
        channels_with_messages: int = 0
        try:
            activity_count, channels_with_messages = await self._count_slack_activity_stats()
            logger.info(
                "[Slack Sync] DB activity stats org=%s messages=%d distinct_channels=%d channels_joined=%d",
                self.organization_id,
                activity_count,
                channels_with_messages,
                channels_joined,
            )
        except Exception as exc:
            logger.warning(
                "[Slack Sync] Failed to count Slack activities in DB org=%s: %s",
                self.organization_id,
                exc,
                exc_info=True,
            )

        # Broadcast completion (joined = new channels this run)
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=joined_count,
            status="completed",
        )

        return {
            "accounts": 0,
            "deals": 0,
            "contacts": 0,
            "activities": activity_count,
            "channels": channels_with_messages,
            "channels_joined": channels_joined,
        }

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Slack doesn't have deals."""
        return {"error": "Slack does not support deals"}

    async def get_user_presence(self, user_id: str) -> dict[str, Any]:
        """Get a user's presence status."""
        data = await self._make_request(
            "GET", "users.getPresence", params={"user": user_id}
        )
        return {
            "presence": data.get("presence"),
            "online": data.get("online", False),
            "auto_away": data.get("auto_away", False),
        }

    async def add_reaction(
        self,
        channel: str,
        timestamp: str,
        emoji: str = "eyes",
    ) -> None:
        """Add an emoji reaction to a message (best-effort)."""
        logger.info(
            "[slack] add_reaction called channel=%s timestamp=%s emoji=%s",
            channel,
            timestamp,
            emoji,
        )
        try:
            await self._make_request(
                "POST",
                "reactions.add",
                json_data={"channel": channel, "timestamp": timestamp, "name": emoji},
            )
            logger.info("[slack] add_reaction succeeded channel=%s timestamp=%s", channel, timestamp)
        except Exception as exc:
            logger.warning(
                "[slack] add_reaction failed channel=%s timestamp=%s emoji=%s error=%s",
                channel,
                timestamp,
                emoji,
                exc,
            )

    async def remove_reaction(
        self,
        channel: str,
        timestamp: str,
        emoji: str = "eyes",
    ) -> None:
        """Remove an emoji reaction from a message."""
        try:
            await self._make_request(
                "POST",
                "reactions.remove",
                json_data={"channel": channel, "timestamp": timestamp, "name": emoji},
            )
        except Exception:
            # Silently ignore if reaction was already removed or doesn't exist
            pass

    async def query(self, request: str) -> dict[str, Any]:
        """Execute a read-only query against Slack."""
        stripped = request.strip()
        if stripped.lower().replace(" ", "_") in ("list_channels", "channels", "get_channels"):
            channels = await self.get_channels()
            return {
                "total": len(channels),
                "channels": [
                    {"id": ch.get("id"), "name": ch.get("name"), "is_private": ch.get("is_private", False)}
                    for ch in channels
                ],
            }
        if stripped.lower().startswith("channel_info:"):
            _, _, channel_id = stripped.partition(":")
            channel_id = channel_id.strip()
            info = await self.get_channel_info(channel_id)
            if info:
                return {"channel": {"id": info.get("id"), "name": info.get("name"), "is_private": info.get("is_private", False), "topic": (info.get("topic") or {}).get("value", ""), "purpose": (info.get("purpose") or {}).get("value", "")}}
            return {"error": f"Channel {channel_id} not found"}
        if stripped.lower().startswith("read_file:"):
            _, _, file_ref = stripped.partition(":")
            file_ref = file_ref.strip()
            if not file_ref:
                return {"error": "read_file requires a file ID or url_private URL"}
            return await self._read_file_for_query(file_ref)
        return {"error": f"Unknown query: {request}. Supported: list_channels, channel_info:<channel_id>, read_file:<file_id_or_url>"}

    async def _read_file_for_query(self, file_ref: str) -> dict[str, Any]:
        """Download and normalize Slack file content for ``query_on_connector``."""
        normalized_ref: str = file_ref.strip()
        if normalized_ref.startswith("id="):
            normalized_ref = normalized_ref[3:].strip()
        if normalized_ref.startswith("url="):
            normalized_ref = normalized_ref[4:].strip()

        file_data: dict[str, Any] | None = None
        download_url: str | None = None

        if normalized_ref.startswith("http://") or normalized_ref.startswith("https://"):
            permalink_payload: tuple[str, str] | None = self._parse_slack_message_permalink(
                normalized_ref
            )
            if permalink_payload is not None:
                channel_id, message_ts = permalink_payload
                return await self._read_files_from_message_permalink(
                    channel_id=channel_id,
                    message_ts=message_ts,
                    permalink=normalized_ref,
                )
            download_url = normalized_ref
        else:
            file_data = await self._make_request("GET", "files.info", params={"file": normalized_ref})
            if not file_data.get("ok"):
                return {"error": f"Slack files.info failed for file {normalized_ref}"}
            file_obj: dict[str, Any] = file_data.get("file") or {}
            download_url = file_obj.get("url_private_download") or file_obj.get("url_private")
            file_data = file_obj

        if not download_url:
            return {"error": f"No download URL available for Slack file reference: {file_ref}"}

        raw_bytes: bytes = await self.download_file(download_url)
        filename: str = str((file_data or {}).get("name") or "slack_file")
        mime_type: str = str((file_data or {}).get("mimetype") or "application/octet-stream")
        size: int = len(raw_bytes)

        content_text: str | None = None
        if mime_type.startswith("text/") or filename.lower().endswith((".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml")):
            content_text = raw_bytes.decode("utf-8", errors="replace")
        elif mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
            from services.file_handler import StoredFile, pdf_to_text_block

            block = pdf_to_text_block(
                StoredFile(
                    upload_id="slack_query_file",
                    filename=filename,
                    mime_type=mime_type,
                    size=size,
                    data=raw_bytes,
                )
            )
            content_text = str(block.get("text") or "")

        if content_text is not None:
            max_chars = 200_000
            if len(content_text) > max_chars:
                content_text = (
                    content_text[:max_chars]
                    + f"\n\n[Truncated — first {max_chars:,} characters shown of {len(content_text):,}]"
                )
            return {
                "ok": True,
                "file": {
                    "filename": filename,
                    "mime_type": mime_type,
                    "size": size,
                    "content": content_text,
                },
            }

        encoded: str = base64.standard_b64encode(raw_bytes).decode("ascii")
        return {
            "ok": True,
            "file": {
                "filename": filename,
                "mime_type": mime_type,
                "size": size,
                "content_base64": encoded,
                "note": "Binary file returned as base64 because text extraction is not supported for this mime type.",
            },
        }

    def _parse_slack_message_permalink(self, url: str) -> tuple[str, str] | None:
        """Return ``(channel_id, message_ts)`` when URL is a Slack message permalink."""
        try:
            parsed = urlparse(url)
        except Exception:
            return None

        if not parsed.netloc.lower().endswith("slack.com"):
            return None

        match = _SLACK_MESSAGE_PERMALINK_RE.match(parsed.path)
        if not match:
            return None

        channel_id: str = str(match.group("channel") or "").upper()
        raw_ts: str = str(match.group("raw_ts") or "").strip()
        if not channel_id or len(raw_ts) < 7:
            return None

        normalized_ts: str = f"{raw_ts[:-6]}.{raw_ts[-6:]}"
        return channel_id, normalized_ts

    async def _read_files_from_message_permalink(
        self,
        *,
        channel_id: str,
        message_ts: str,
        permalink: str,
    ) -> dict[str, Any]:
        """Resolve files attached to a Slack message permalink and return extracted payloads."""
        logger.info(
            "[slack] read_file resolving message permalink channel=%s ts=%s",
            channel_id,
            message_ts,
        )
        try:
            messages: list[dict[str, Any]] = await self.get_channel_messages(
                channel_id=channel_id,
                latest=message_ts,
                limit=1,
                inclusive=True,
            )
        except Exception as exc:
            logger.warning(
                "[slack] Failed to resolve permalink to message channel=%s ts=%s error=%s",
                channel_id,
                message_ts,
                exc,
            )
            return {
                "error": (
                    f"Slack could not resolve message permalink {permalink}: {exc}"
                )
            }

        target_message: dict[str, Any] | None = None
        for message in messages:
            if str(message.get("ts") or "").strip() == message_ts:
                target_message = message
                break
        if target_message is None:
            return {"error": f"No Slack message found for permalink: {permalink}"}

        files: list[dict[str, Any]] = target_message.get("files") or []
        if not files:
            return {"error": f"No files are attached to Slack message permalink: {permalink}"}

        extracted_files: list[dict[str, Any]] = []
        for file_entry in files:
            file_id: str = str(file_entry.get("id") or "").strip()
            file_url: str = str(
                file_entry.get("url_private_download")
                or file_entry.get("url_private")
                or ""
            ).strip()
            if not file_id and not file_url:
                continue
            lookup_ref: str = file_id or file_url
            file_result: dict[str, Any] = await self._read_file_for_query(lookup_ref)
            if file_result.get("ok") and isinstance(file_result.get("file"), dict):
                extracted_files.append(file_result["file"])

        if not extracted_files:
            return {
                "error": f"Slack message permalink resolved, but no attached files could be downloaded: {permalink}"
            }

        return {
            "ok": True,
            "source": {
                "type": "slack_message_permalink",
                "channel_id": channel_id,
                "ts": message_ts,
                "permalink": permalink,
            },
            "file": extracted_files[0],
            "files": extracted_files,
        }

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a side-effect action."""
        if action == "send_message":
            channel: str | None = params.get("channel")
            user_id: str | None = params.get("user_id")
            text: str = params.get("text") or params.get("message") or ""
            thread_ts: str | None = params.get("thread_ts")
            if not str(text).strip():
                raise ValueError("send_message requires non-empty text")
            if user_id and not channel:
                return await self.send_direct_message(user_id, text)
            if channel:
                return await self.post_message(channel, text, thread_ts=thread_ts)
            raise ValueError("send_message requires 'channel' or 'user_id' and non-empty text")
        if action == "fetch_channel_history":
            ch: str | None = (
                params.get("channel")
                or params.get("channel_id")
                or params.get("channelId")
            )
            since_val: str | None = params.get("since")
            if not ch or not str(ch).strip():
                logger.error(
                    "[slack] fetch_channel_history missing channel params_keys=%s",
                    sorted(params.keys()),
                )
                raise ValueError("fetch_channel_history requires 'channel' (or 'channel_id')")
            if not since_val or not str(since_val).strip():
                logger.error(
                    "[slack] fetch_channel_history missing since channel=%s params_keys=%s",
                    ch,
                    sorted(params.keys()),
                )
                raise ValueError("fetch_channel_history requires 'since' (ISO 8601 datetime)")
            limit_raw: Any = params.get("limit", 1000)
            limit_val: int = int(limit_raw) if limit_raw is not None else 1000
            logger.info(
                "[slack] execute_action fetch_channel_history channel=%s since=%s limit=%s",
                ch,
                since_val,
                limit_val,
            )
            return await self.fetch_channel_history(
                str(ch).strip(),
                str(since_val).strip(),
                limit=limit_val,
            )
        raise ValueError(f"Unknown action: {action}")

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
        blocks: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """
        Post a message to a Slack channel.
        
        Args:
            channel: Channel ID or name (e.g., "#general" or "C1234567890")
            text: Message text (used as fallback if blocks provided)
            thread_ts: Optional thread timestamp to reply in thread
            blocks: Optional Block Kit blocks for rich formatting
        
        Returns:
            Response with channel, ts (timestamp), and message details
        """
        channel = await self._resolve_channel_for_post(channel)

        # When blocks are provided, text is already mrkdwn from the caller. Otherwise convert.
        if blocks is not None:
            formatted_text: str = text
        else:
            formatted_text, _ = markdown_to_mrkdwn(text)

        normalized_text: str = formatted_text.strip()
        if not normalized_text and blocks:
            normalized_text = _extract_fallback_text_from_blocks(blocks)
            if normalized_text:
                logger.info(
                    "[SlackConnector] Derived fallback text from blocks for chat.postMessage channel=%s chars=%d",
                    channel,
                    len(normalized_text),
                )
            else:
                normalized_text = "Notification"
                logger.warning(
                    "[SlackConnector] Missing fallback text and no extractable text in blocks; using default fallback channel=%s",
                    channel,
                )
        if not normalized_text:
            logger.error(
                "[SlackConnector] Refusing to post empty Slack message channel=%s has_blocks=%s",
                channel,
                bool(blocks),
            )
            raise ValueError("Slack message text must be non-empty")

        payload: dict[str, Any] = {
            "channel": channel,
            "text": normalized_text,
        }
        
        if thread_ts:
            payload["thread_ts"] = thread_ts
        
        if blocks:
            payload["blocks"] = blocks
        
        try:
            data = await self._make_request("POST", "chat.postMessage", json_data=payload)
        except ValueError as exc:
            error_message = str(exc)
            if "channel_not_found" not in error_message:
                raise

            logger.warning(
                "[SlackConnector] chat.postMessage returned channel_not_found for channel=%s user_id=%s; retrying with org-level credentials",
                channel,
                self.user_id,
            )
            data = await self._retry_post_message_with_org_credentials(payload, original_error=exc)
        
        return {
            "ok": data.get("ok"),
            "channel": data.get("channel"),
            "ts": data.get("ts"),
            "message": data.get("message"),
        }

    async def _retry_post_message_with_org_credentials(
        self,
        payload: dict[str, Any],
        *,
        original_error: ValueError,
    ) -> dict[str, Any]:
        """Retry chat.postMessage with non-user-scoped credentials for this org."""
        original_user_id = self.user_id
        original_token = self._token
        original_integration = self._integration

        try:
            self.user_id = None
            self._token = None
            self._integration = None
            return await self._make_request("POST", "chat.postMessage", json_data=payload)
        except Exception:
            logger.warning(
                "[SlackConnector] Org-level credential retry failed for channel=%s after channel_not_found",
                payload.get("channel"),
                exc_info=True,
            )
            raise original_error
        finally:
            self.user_id = original_user_id
            self._token = original_token
            self._integration = original_integration

    async def _resolve_channel_for_post(self, channel: str) -> str:
        """Resolve a human channel name (e.g. #general) to a channel ID when possible."""
        normalized_channel = str(channel).strip()
        if not normalized_channel.startswith("#"):
            return normalized_channel

        target_name = normalized_channel[1:]
        try:
            channels = await self.get_channels()
        except Exception as exc:
            logger.warning(
                "[SlackConnector] Could not resolve channel name=%s before posting: %s",
                normalized_channel,
                exc,
            )
            return normalized_channel

        for candidate in channels:
            candidate_names = {
                str(candidate.get("name") or ""),
                str(candidate.get("name_normalized") or ""),
            }
            if target_name in candidate_names and candidate.get("id"):
                resolved = str(candidate["id"])
                logger.info(
                    "[SlackConnector] Resolved channel name=%s to channel_id=%s",
                    normalized_channel,
                    resolved,
                )
                return resolved

        logger.warning(
            "[SlackConnector] Could not resolve channel name=%s to an ID; posting as-is",
            normalized_channel,
        )
        return normalized_channel

    async def upload_file(
        self,
        channel: str,
        content: bytes,
        filename: str,
        title: str,
        initial_comment: str = "",
        thread_ts: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Upload a file to Slack using files.getUploadURLExternal + upload URL + files.completeUploadExternal.

        Requires files:write scope. Shares the file in the given channel with optional initial comment and thread.

        Args:
            channel: Channel ID (or resolved name) where the file will be shared.
            content: Raw file bytes.
            filename: Name of the file (e.g. "data.csv").
            title: Display title for the file in Slack.
            initial_comment: Optional message introducing the file in the channel.
            thread_ts: Optional thread timestamp to post the file as a reply.

        Returns:
            Response with ok, files (list of {id, title}), and any Slack metadata.
        """
        channel_id: str = await self._resolve_channel_for_post(channel)
        length: int = len(content)

        get_url_data: dict[str, Any] = await self._make_request(
            "POST",
            "files.getUploadURLExternal",
            json_data={"filename": filename, "length": length},
        )
        upload_url: str = get_url_data.get("upload_url") or ""
        file_id: str = get_url_data.get("file_id") or ""
        if not upload_url or not file_id:
            raise ValueError(
                "Slack files.getUploadURLExternal did not return upload_url and file_id"
            )

        async with httpx.AsyncClient() as client:
            files: dict[str, tuple[str, bytes]] = {"file": (filename, content)}
            upload_resp: httpx.Response = await client.post(
                upload_url, files=files, timeout=60.0
            )
            upload_resp.raise_for_status()

        complete_payload: dict[str, Any] = {
            "files": [{"id": file_id, "title": title}],
            "channel_id": channel_id,
        }
        if thread_ts:
            complete_payload["thread_ts"] = thread_ts
        if initial_comment:
            complete_payload["initial_comment"] = initial_comment

        data: dict[str, Any] = await self._make_request(
            "POST", "files.completeUploadExternal", json_data=complete_payload
        )
        return {
            "ok": data.get("ok"),
            "files": data.get("files", []),
        }

    async def download_file(self, url_private: str) -> bytes:
        """
        Download a file from Slack using the bot token for authentication.

        Slack's ``url_private`` / ``url_private_download`` URLs require an
        Authorization header with the bot token.

        Args:
            url_private: The ``url_private_download`` (preferred) or
                ``url_private`` URL from a Slack file object.

        Returns:
            Raw file bytes.

        Raises:
            httpx.HTTPStatusError: If the download request fails.
            ValueError: If the response body is empty.
        """
        headers: dict[str, str] = await self._get_headers()
        # Remove Content-Type for raw file download
        headers.pop("Content-Type", None)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            response: httpx.Response = await client.get(
                url_private, headers=headers, timeout=60.0,
            )
            response.raise_for_status()

        data: bytes = response.content
        if not data:
            raise ValueError(f"Empty response downloading Slack file: {url_private}")
        return data

    async def _send_direct_message_once(
        self,
        slack_user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Open a DM channel for one Slack user ID and send a message."""
        logger.info("[SlackConnector] Opening DM for slack_user_id=%s", slack_user_id)
        try:
            open_data = await self._make_request(
                "POST",
                "conversations.open",
                json_data={"users": slack_user_id},
            )
        except ValueError as exc:
            if "missing_scope" not in str(exc):
                raise
            logger.warning(
                "[SlackConnector] conversations.open missing_scope for slack_user_id=%s; "
                "falling back to chat.postMessage(channel=user_id)",
                slack_user_id,
            )
            return await self.post_message(channel=slack_user_id, text=text)

        channel_id = (open_data.get("channel") or {}).get("id")
        if not channel_id:
            raise ValueError("Slack API error: missing DM channel id")

        logger.info(
            "[SlackConnector] Posting DM to slack_user_id=%s channel=%s",
            slack_user_id,
            channel_id,
        )
        return await self.post_message(channel=channel_id, text=text)

    async def send_direct_message(
        self,
        slack_user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Open a DM channel and send a direct message.

        If Slack rejects the provided user ID with ``user_not_found``, try any
        sibling Slack identities we know for the same person before letting the
        caller fall back to a broader channel announcement.
        """
        normalized_slack_user_id = str(slack_user_id).strip().upper()
        from services.slack_identity import (
            demote_slack_user_id_preference,
            get_alternate_slack_user_ids_for_identity,
            mark_slack_user_id_preferred,
        )

        try:
            response = await self._send_direct_message_once(normalized_slack_user_id, text)
            await mark_slack_user_id_preferred(
                organization_id=self.organization_id,
                slack_user_id=normalized_slack_user_id,
            )
            return response
        except ValueError as exc:
            if "user_not_found" not in str(exc):
                raise
            logger.warning(
                "[SlackConnector] DM target user_not_found for slack_user_id=%s org=%s; checking alternate identities",
                normalized_slack_user_id,
                self.organization_id,
            )
            await demote_slack_user_id_preference(
                organization_id=self.organization_id,
                slack_user_id=normalized_slack_user_id,
            )

        alternate_user_ids = await get_alternate_slack_user_ids_for_identity(
            organization_id=self.organization_id,
            slack_user_id=normalized_slack_user_id,
        )
        last_error: Exception = ValueError(f"Slack API error: user_not_found ({normalized_slack_user_id})")
        for idx, alternate_user_id in enumerate(alternate_user_ids, start=1):
            try:
                logger.info(
                    "[SlackConnector] Retrying DM via alternate Slack identity org=%s original=%s alternate=%s attempt=%d/%d",
                    self.organization_id,
                    normalized_slack_user_id,
                    alternate_user_id,
                    idx,
                    len(alternate_user_ids),
                )
                response = await self._send_direct_message_once(alternate_user_id, text)
                await mark_slack_user_id_preferred(
                    organization_id=self.organization_id,
                    slack_user_id=alternate_user_id,
                )
                return response
            except Exception as exc:  # noqa: BLE001 - keep trying alternates
                last_error = exc
                if "user_not_found" in str(exc):
                    await demote_slack_user_id_preference(
                        organization_id=self.organization_id,
                        slack_user_id=alternate_user_id,
                    )
                logger.warning(
                    "[SlackConnector] Alternate Slack DM failed org=%s original=%s alternate=%s attempt=%d/%d error=%s",
                    self.organization_id,
                    normalized_slack_user_id,
                    alternate_user_id,
                    idx,
                    len(alternate_user_ids),
                    exc,
                    exc_info=True,
                )

        raise last_error
