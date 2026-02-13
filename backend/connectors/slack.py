"""
Slack connector implementation.

Responsibilities:
- Authenticate with Slack using OAuth token
- Fetch channels, messages, and user activity
- Normalize Slack data to activity records
- Handle pagination and rate limits
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert


def markdown_to_mrkdwn(text: str) -> str:
    """
    Convert standard Markdown to Slack mrkdwn format.
    
    Key differences:
    - Bold: **text** → *text*
    - Italic: *text* → _text_ (when not already bold)
    - Links: [text](url) → <url|text>
    - Headers: # Header → *Header*
    - Tables: Wrapped in code blocks (Slack doesn't support tables)
    """
    # Convert markdown tables to code blocks (Slack doesn't support tables)
    # Match table pattern: lines starting with | and containing |
    table_pattern = r'((?:^\|.+\|$\n?)+)'
    
    def wrap_table_in_code_block(match: re.Match[str]) -> str:
        table = match.group(1)
        # Remove the separator row (|---|---|) as it's just visual noise in monospace
        lines = table.strip().split('\n')
        filtered_lines: list[str] = []
        for line in lines:
            # Skip separator rows like |---|---| or | --- | --- |
            if not re.match(r'^\|[\s\-:]+\|$', line.strip()):
                filtered_lines.append(line)
        return '```\n' + '\n'.join(filtered_lines) + '\n```'
    
    text = re.sub(table_pattern, wrap_table_in_code_block, text, flags=re.MULTILINE)
    
    # Convert bold: **text** → *text*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    
    # Convert markdown links: [text](url) → <url|text>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
    
    # Convert headers: # Header → *Header*
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    
    return text

from api.websockets import broadcast_sync_progress
from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session
from models.user import User

SLACK_API_BASE = "https://slack.com/api"
logger = logging.getLogger(__name__)


class SlackConnector(BaseConnector):
    """Connector for Slack workspace data."""

    source_system = "slack"

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Slack API."""
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Slack API."""
        headers = await self._get_headers()
        url = f"{SLACK_API_BASE}/{endpoint}"

        async with httpx.AsyncClient() as client:
            if method == "GET":
                response = await client.get(
                    url, headers=headers, params=params, timeout=30.0
                )
            else:
                response = await client.post(
                    url, headers=headers, json=json_data, timeout=30.0
                )

            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                raise ValueError(f"Slack API error: {data.get('error', 'Unknown')}")

            return data

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

    async def get_current_user_profile(self) -> dict[str, Any]:
        """Get the authenticated user's profile via users.profile.get."""
        data = await self._make_request("GET", "users.profile.get")
        return data.get("profile", {})

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

    async def sync_activities(self) -> int:
        """
        Sync Slack messages as activities.

        This captures communication activity that can be correlated
        with deals and accounts.
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

        # Calculate timestamp for last 7 days
        oldest = (datetime.utcnow().timestamp()) - (7 * 24 * 60 * 60)

        count = 0
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
                    messages = await self.get_channel_messages(
                        channel_id, oldest=oldest, limit=100
                    )

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

        return count

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
        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=source_id,
            type="slack_message",
            subject=f"#{channel_name}",
            description=text[:1000],  # Truncate very long messages
            activity_date=activity_date,
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
        from services.slack_conversations import (
            refresh_slack_user_mappings_from_directory,
            refresh_slack_user_mappings_for_org,
            upsert_slack_user_mapping_from_nango_action,
            upsert_slack_user_mapping_from_current_profile,
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
                "[Slack Sync] Fetching current Slack user profile for org=%s",
                self.organization_id,
            )
            mapped_count = await upsert_slack_user_mapping_from_current_profile(
                organization_id=self.organization_id,
                connector=self,
                integration=self._integration,
            )
            logger.info(
                "[Slack Sync] Upserted %d Slack user mappings from current profile for org=%s",
                mapped_count,
                self.organization_id,
            )
        except Exception as exc:
            logger.warning(
                "[Slack Sync] Failed to map current Slack user profile for org=%s: %s",
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

        activities_count = await self.sync_activities()

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
        }

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Slack doesn't have deals."""
        return {"error": "Slack does not support deals"}

    async def search_messages(
        self,
        query: str,
        count: int = 20,
    ) -> list[dict[str, Any]]:
        """Search for messages matching a query."""
        params = {
            "query": query,
            "count": count,
            "sort": "timestamp",
            "sort_dir": "desc",
        }

        data = await self._make_request("GET", "search.messages", params=params)
        return data.get("messages", {}).get("matches", [])

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
        try:
            await self._make_request(
                "POST",
                "reactions.add",
                json_data={"channel": channel, "timestamp": timestamp, "name": emoji},
            )
        except Exception:
            # Silently ignore — reactions require the reactions:write scope
            # which may not be granted yet.
            pass

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
        
        data = await self._make_request("POST", "chat.postMessage", json_data=payload)
        
        return {
            "ok": data.get("ok"),
            "channel": data.get("channel"),
            "ts": data.get("ts"),
            "message": data.get("message"),
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

    async def send_direct_message(
        self,
        slack_user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Open a DM channel and send a direct message."""
        logger.info("[SlackConnector] Opening DM for slack_user_id=%s", slack_user_id)
        open_data = await self._make_request(
            "POST",
            "conversations.open",
            json_data={"users": slack_user_id},
        )
        channel_id = (open_data.get("channel") or {}).get("id")
        if not channel_id:
            raise ValueError("Slack API error: missing DM channel id")

        logger.info(
            "[SlackConnector] Posting DM to slack_user_id=%s channel=%s",
            slack_user_id,
            channel_id,
        )
        return await self.post_message(channel=channel_id, text=text)
