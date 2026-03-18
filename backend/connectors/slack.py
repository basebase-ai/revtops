"""
Slack connector implementation.

Responsibilities:
- Authenticate with Slack using OAuth token
- Fetch channels, messages, and user activity
- Normalize Slack data to activity records
- Handle pagination and rate limits
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert


_SEPARATOR_ROW_RE: re.Pattern[str] = re.compile(r'^\|[\s\-:]+\|$')


def _clean_table_lines(raw: str) -> str:
    """Strip markdown separator rows from a pipe-delimited table."""
    lines: list[str] = raw.strip().split('\n')
    filtered: list[str] = [
        line for line in lines if not _SEPARATOR_ROW_RE.match(line.strip())
    ]
    return '\n'.join(filtered)


def markdown_to_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn format.

    Handles bold, italic, links, headers, and tables.  Existing fenced code
    blocks are extracted first so their contents are never double-processed.
    """

    # -- Step 1: extract fenced code blocks into placeholders ---------------
    code_blocks: list[str] = []
    _FENCE_RE: re.Pattern[str] = re.compile(r'```\w*\n(.*?)```', re.DOTALL)

    def _extract_fence(match: re.Match[str]) -> str:
        content: str = match.group(1)
        cleaned: str = _clean_table_lines(content) if '|' in content else content.strip()
        idx: int = len(code_blocks)
        code_blocks.append('```\n' + cleaned + '\n```')
        return f'\x00CB{idx}\x00'

    text = _FENCE_RE.sub(_extract_fence, text)

    # -- Step 2: wrap bare markdown tables that weren't already fenced ------
    _TABLE_RE: re.Pattern[str] = re.compile(r'((?:^\|.+\|$\n?)+)', re.MULTILINE)

    def _wrap_table(match: re.Match[str]) -> str:
        return '```\n' + _clean_table_lines(match.group(1)) + '\n```'

    text = _TABLE_RE.sub(_wrap_table, text)

    # -- Step 3: inline formatting ------------------------------------------
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)

    # -- Step 4: restore code blocks ----------------------------------------
    for i, block in enumerate(code_blocks):
        text = text.replace(f'\x00CB{i}\x00', block)

    return text

from api.websockets import broadcast_sync_progress
from connectors.base import BaseConnector
from connectors.registry import (
    AuthType, Capability, ConnectorAction, ConnectorMeta, ConnectorScope,
)
from models.activity import Activity
from models.database import get_session
from models.integration import Integration
from models.user import User

SLACK_API_BASE = "https://slack.com/api"
logger = logging.getLogger(__name__)


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
""",
    )

    def __init__(
        self,
        organization_id: str,
        user_id: str | None = None,
        team_id: str | None = None,
    ) -> None:
        """Initialize Slack connector.

        Args:
            organization_id: Organization UUID.
            user_id: Optional owner user UUID.
            team_id: Optional Slack team/workspace ID used to disambiguate
                between multiple Slack integrations in the same org.
        """
        super().__init__(organization_id=organization_id, user_id=user_id)
        self.team_id = (team_id or "").strip() or None

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

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Slack API with rate-limit retry."""
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
                    logger.warning(
                        "[Slack API] 429 rate-limited on %s (attempt %d/%d), retrying in %.1fs",
                        endpoint,
                        attempt + 1,
                        self._MAX_RETRIES,
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()
                data: dict[str, Any] = response.json()

                if not data.get("ok"):
                    raise ValueError(f"Slack API error: {data.get('error', 'Unknown')}")

                return data

        raise httpx.HTTPStatusError(
            "Rate limited after max retries",
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

    async def sync_activities(self) -> tuple[int, int]:
        """
        Sync Slack messages as activities.

        Returns:
            Tuple of (activities_count, channels_with_messages_count).
        """
        logger.info("[Slack Sync] Starting Slack activity sync for org=%s", self.organization_id)
        # Broadcast that we're starting
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
        )
        
        # Get channels
        channels = await self.get_channels()
        logger.info(
            "[Slack Sync] Retrieved %d channels for org=%s",
            len(channels),
            self.organization_id,
        )

        oldest: float = self.sync_since.timestamp() if self.sync_since else (datetime.utcnow().timestamp() - 7 * 24 * 60 * 60)

        count = 0
        channels_with_messages = 0
        user_info_cache: dict[str, dict[str, Any]] = {}
        async with get_session(organization_id=self.organization_id) as session:
            for channel in channels:
                channel_id = channel.get("id", "")
                channel_name = channel.get("name", "unknown")
                logger.debug(
                    "[Slack Sync] Fetching messages for channel=%s (%s)",
                    channel_name,
                    channel_id,
                )

                try:
                    # Join public channels so we can read history (requires channels:join scope)
                    is_private: bool = bool(channel.get("is_private", True))
                    if not is_private:
                        await self.join_channel(channel_id)
                    messages = await self.get_channel_messages(
                        channel_id, oldest=oldest, limit=100
                    )
                    if messages:
                        channels_with_messages += 1

                    for msg in messages:
                        user_id = msg.get("user")
                        if user_id and not msg.get("user_profile"):
                            if user_id not in user_info_cache:
                                try:
                                    user_info_cache[user_id] = await self.get_user_info(user_id)
                                except Exception as exc:
                                    logger.warning(
                                        "[Slack Sync] Failed users.info lookup for user=%s channel=%s: %s",
                                        user_id,
                                        channel_id,
                                        exc,
                                        exc_info=True,
                                    )
                                    user_info_cache[user_id] = {}
                            profile = (user_info_cache[user_id] or {}).get("profile") or {}
                            if profile:
                                msg["user_profile"] = profile

                        activity = self._normalize_message(msg, channel_id, channel_name)
                        if activity:
                            now: datetime = datetime.utcnow()
                            logger.debug(
                                "[Slack Sync] Upserting message source_id=%s channel=%s ts=%s",
                                activity.source_id,
                                channel_id,
                                msg.get("ts"),
                            )
                            stmt = (
                                pg_insert(Activity)
                                .values(
                                    id=activity.id,
                                    organization_id=activity.organization_id,
                                    source_system=activity.source_system,
                                    source_id=activity.source_id,
                                    type=activity.type,
                                    subject=activity.subject,
                                    description=activity.description,
                                    activity_date=activity.activity_date,
                                    custom_fields=activity.custom_fields,
                                    synced_at=now,
                                )
                                .on_conflict_do_update(
                                    index_elements=[
                                        "organization_id",
                                        "source_system",
                                        "source_id",
                                    ],
                                    index_where=Activity.source_id.is_not(None),
                                    set_={
                                        "subject": activity.subject,
                                        "description": activity.description,
                                        "custom_fields": activity.custom_fields,
                                        "activity_date": activity.activity_date,
                                        "synced_at": now,
                                    },
                                )
                            )
                            await session.execute(stmt)
                            count += 1
                            
                            # Broadcast progress every 10 messages
                            if count % 10 == 0:
                                await broadcast_sync_progress(
                                    organization_id=self.organization_id,
                                    provider=self.source_system,
                                    count=count,
                                    status="syncing",
                                )

                except Exception as e:
                    # Skip channels we can't access
                    logger.warning(
                        "[Slack Sync] Error fetching messages from channel=%s (%s): %s",
                        channel_name,
                        channel_id,
                        e,
                        exc_info=True,
                    )
                    continue

            await session.commit()

        return count, channels_with_messages

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
            logger.info(
                "[Slack Sync] Refreshing Slack user mappings before activity sync for org=%s",
                self.organization_id,
            )
            logger.info(
                "[Slack Sync] Refreshing Slack directory user mappings for org=%s",
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

        activities_count, channels_count = await self.sync_activities()

        # Broadcast completion
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=activities_count,
            status="completed",
        )

        return {
            "accounts": 0,
            "deals": 0,
            "contacts": 0,
            "activities": activities_count,
            "channels": channels_count,
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
        return {"error": f"Unknown query: {request}. Supported: list_channels, channel_info:<channel_id>"}

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

        # Auto-convert any Markdown to Slack mrkdwn format
        formatted_text = markdown_to_mrkdwn(text)
        
        payload: dict[str, Any] = {
            "channel": channel,
            "text": formatted_text,
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

    async def send_direct_message(
        self,
        slack_user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Open a DM channel and send a direct message."""
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
