"""
Slack connector implementation.

Responsibilities:
- Authenticate with Slack using OAuth token
- Fetch channels, messages, and user activity
- Normalize Slack data to activity records
- Handle pagination and rate limits
"""

import uuid
from datetime import datetime
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session

SLACK_API_BASE = "https://slack.com/api"


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
        # Get channels
        channels = await self.get_channels()

        # Calculate timestamp for last 7 days
        oldest = (datetime.utcnow().timestamp()) - (7 * 24 * 60 * 60)

        count = 0
        async with get_session() as session:
            for channel in channels:
                channel_id = channel.get("id", "")
                channel_name = channel.get("name", "unknown")

                try:
                    messages = await self.get_channel_messages(
                        channel_id, oldest=oldest, limit=100
                    )

                    for msg in messages:
                        activity = self._normalize_message(msg, channel_id, channel_name)
                        if activity:
                            await session.merge(activity)
                            count += 1

                except Exception as e:
                    # Skip channels we can't access
                    print(f"Error fetching messages from {channel_name}: {e}")
                    continue

            await session.commit()

        return count

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

        return Activity(
            id=uuid.uuid4(),
            customer_id=uuid.UUID(self.customer_id),
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
                "thread_ts": slack_msg.get("thread_ts"),
                "has_attachments": len(slack_msg.get("attachments", [])) > 0,
                "has_files": len(slack_msg.get("files", [])) > 0,
            },
        )

    async def sync_all(self) -> dict[str, int]:
        """Run all sync operations."""
        activities_count = await self.sync_activities()

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
