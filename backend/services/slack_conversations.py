"""
Slack conversation service.

Handles processing incoming Slack messages (DMs, @mentions, thread replies)
and routing them through the agent orchestrator.  Also persists inbound
channel messages as Activity rows for real-time queryability.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.orchestrator import ChatOrchestrator
from connectors.slack import SlackConnector
from models.activity import Activity
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.integration import Integration

logger = logging.getLogger(__name__)


async def find_organization_by_slack_team(team_id: str) -> str | None:
    """
    Find the organization ID for a Slack team/workspace.
    
    Matches the team_id to an active Slack integration's external data.
    Uses admin session to bypass RLS since we don't know the org yet.
    
    Args:
        team_id: Slack workspace/team ID (e.g., "T04ABCDEF")
        
    Returns:
        Organization ID string or None if not found
    """
    async with get_admin_session() as session:
        # Find Slack integration with matching team_id in extra_data
        # The team_id is stored when the integration is connected via Nango
        query = (
            select(Integration)
            .where(Integration.provider == "slack")
            .where(Integration.is_active == True)
        )
        result = await session.execute(query)
        integrations = result.scalars().all()
        
        for integration in integrations:
            # Check if team_id matches in extra_data
            extra_data = integration.extra_data or {}
            if extra_data.get("team_id") == team_id:
                return str(integration.organization_id)
            
            # Also check nango_connection_id which contains the org_id
            # Format: "{org_id}" for org-scoped integrations
            if integration.nango_connection_id:
                # The connection ID itself is the org_id for org-scoped integrations
                # We can use this to find which org owns this integration
                return str(integration.organization_id)
        
        # Fallback: if there's only one Slack integration, use it
        # This handles cases where team_id isn't stored
        if len(integrations) == 1:
            logger.warning(
                "[slack_conversations] No team_id match, using only Slack integration for team %s",
                team_id
            )
            return str(integrations[0].organization_id)
    
    return None


async def find_or_create_conversation(
    organization_id: str,
    slack_channel_id: str,
    slack_user_id: str,
) -> Conversation:
    """
    Find an existing Slack conversation or create a new one.
    
    Conversations are keyed by (source='slack', source_channel_id).
    
    Args:
        organization_id: The organization this conversation belongs to
        slack_channel_id: Slack DM channel ID
        slack_user_id: Slack user ID who initiated the conversation
        
    Returns:
        Existing or new Conversation instance
    """
    async with get_session(organization_id=organization_id) as session:
        # Try to find existing conversation for this DM channel
        query = (
            select(Conversation)
            .where(Conversation.organization_id == UUID(organization_id))
            .where(Conversation.source == "slack")
            .where(Conversation.source_channel_id == slack_channel_id)
        )
        result = await session.execute(query)
        conversation = result.scalar_one_or_none()
        
        if conversation:
            logger.info(
                "[slack_conversations] Found existing conversation %s for channel %s",
                conversation.id,
                slack_channel_id
            )
            return conversation
        
        # Create new conversation for this Slack DM
        conversation = Conversation(
            organization_id=UUID(organization_id),
            user_id=None,  # No RevTops user for Slack conversations
            source="slack",
            source_channel_id=slack_channel_id,
            source_user_id=slack_user_id,
            type="agent",
            title=f"Slack DM",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        
        logger.info(
            "[slack_conversations] Created new conversation %s for channel %s",
            conversation.id,
            slack_channel_id
        )
        return conversation


async def find_thread_conversation(
    organization_id: str,
    channel_id: str,
    thread_ts: str,
) -> Conversation | None:
    """
    Look up an existing conversation for a Slack channel thread.

    Returns the conversation if the bot is already participating in this
    thread, or None if not.  Unlike find_or_create_conversation this never
    creates a new row.

    Args:
        organization_id: The organization this conversation belongs to
        channel_id: Slack channel ID
        thread_ts: Thread parent timestamp

    Returns:
        Existing Conversation or None
    """
    source_channel_id: str = f"{channel_id}:{thread_ts}"
    async with get_session(organization_id=organization_id) as session:
        query = (
            select(Conversation)
            .where(Conversation.organization_id == UUID(organization_id))
            .where(Conversation.source == "slack")
            .where(Conversation.source_channel_id == source_channel_id)
        )
        result = await session.execute(query)
        conversation: Conversation | None = result.scalar_one_or_none()
        if conversation:
            logger.debug(
                "[slack_conversations] Found thread conversation %s for %s",
                conversation.id,
                source_channel_id,
            )
        return conversation


async def persist_slack_message_activity(
    team_id: str,
    channel_id: str,
    user_id: str,
    text: str,
    ts: str,
    thread_ts: str | None,
) -> None:
    """
    Persist an inbound Slack channel message as an Activity row.

    Uses INSERT ... ON CONFLICT DO NOTHING so that duplicate messages
    (e.g. from the hourly sync) are silently skipped.

    Args:
        team_id: Slack workspace/team ID
        channel_id: Slack channel ID
        user_id: Slack user ID who sent the message
        text: Message text
        ts: Message timestamp (unique per-message)
        thread_ts: Parent thread timestamp, if this is a threaded reply
    """
    organization_id: str | None = await find_organization_by_slack_team(team_id)
    if not organization_id:
        return

    source_id: str = f"{channel_id}:{ts}"

    # Parse message timestamp into a datetime
    activity_date: datetime | None = None
    try:
        activity_date = datetime.utcfromtimestamp(float(ts))
    except (ValueError, TypeError):
        pass

    try:
        async with get_session(organization_id=organization_id) as session:
            stmt = pg_insert(Activity).values(
                id=uuid.uuid4(),
                organization_id=UUID(organization_id),
                source_system="slack",
                source_id=source_id,
                type="slack_message",
                subject=f"#{channel_id}",
                description=text[:1000],
                activity_date=activity_date,
                custom_fields={
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "thread_ts": thread_ts,
                },
                synced_at=datetime.utcnow(),
            ).on_conflict_do_nothing(
                index_elements=["organization_id", "source_system", "source_id"],
                index_where=text("source_id IS NOT NULL"),
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as e:
        logger.error(
            "[slack_conversations] Failed to persist activity for %s: %s",
            source_id,
            e,
        )


async def _stream_and_post_responses(
    orchestrator: ChatOrchestrator,
    connector: SlackConnector,
    message_text: str,
    channel: str,
    thread_ts: str | None = None,
) -> int:
    """
    Stream orchestrator output and post each text segment to Slack
    incrementally — flushing whenever a tool-call boundary (JSON chunk)
    is encountered so the user sees early messages immediately.

    Args:
        orchestrator: Initialised ChatOrchestrator
        connector: Authenticated SlackConnector
        message_text: The user's message to process
        channel: Slack channel to post responses in
        thread_ts: Optional thread timestamp (None for DM top-level)

    Returns:
        Total character count of all posted text.
    """
    current_text: str = ""
    total_length: int = 0

    try:
        async for chunk in orchestrator.process_message(message_text):
            if chunk.startswith("{"):
                # Tool-call boundary — send whatever text we have so far
                if current_text.strip():
                    await connector.post_message(
                        channel=channel,
                        text=current_text.strip(),
                        thread_ts=thread_ts,
                    )
                    total_length += len(current_text)
                    current_text = ""
            else:
                current_text += chunk
    except Exception as e:
        logger.error(
            "[slack_conversations] Error during streaming: %s", e, exc_info=True,
        )
        current_text += f"\nSorry, I encountered an error: {str(e)}"

    # Post any remaining text after the stream ends
    if current_text.strip():
        await connector.post_message(
            channel=channel,
            text=current_text.strip(),
            thread_ts=thread_ts,
        )
        total_length += len(current_text)

    return total_length


async def process_slack_dm(
    team_id: str,
    channel_id: str,
    user_id: str,
    message_text: str,
    event_ts: str,
) -> dict[str, Any]:
    """
    Process an incoming Slack DM and generate a response.
    
    This is the main entry point for handling Slack DMs:
    1. Find the organization from the Slack team
    2. Find or create a conversation for this DM channel
    3. Process the message through the agent orchestrator
    4. Post the response back to Slack
    
    Args:
        team_id: Slack workspace/team ID
        channel_id: Slack DM channel ID
        user_id: Slack user ID who sent the message
        message_text: The message content
        event_ts: Event timestamp for deduplication
        
    Returns:
        Result dict with status and any error details
    """
    logger.info(
        "[slack_conversations] Processing DM from user %s in channel %s: %s",
        user_id,
        channel_id,
        message_text[:100]
    )
    
    # Find organization from Slack team
    organization_id = await find_organization_by_slack_team(team_id)
    if not organization_id:
        logger.error("[slack_conversations] No organization found for team %s", team_id)
        return {
            "status": "error",
            "error": f"No organization found for Slack team {team_id}"
        }
    
    connector = SlackConnector(organization_id=organization_id)

    # Show a reaction so the user knows the bot is working
    await connector.add_reaction(channel=channel_id, timestamp=event_ts)

    # Find or create conversation
    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=channel_id,
        slack_user_id=user_id,
    )

    # Process message through orchestrator, posting incrementally
    orchestrator = ChatOrchestrator(
        user_id=None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=None,
        workflow_context=None,
    )

    total_length: int = await _stream_and_post_responses(
        orchestrator=orchestrator,
        connector=connector,
        message_text=message_text,
        channel=channel_id,
    )

    # Remove the "thinking" reaction
    await connector.remove_reaction(channel=channel_id, timestamp=event_ts)

    logger.info(
        "[slack_conversations] Posted response to channel %s (%d chars)",
        channel_id,
        total_length,
    )
    return {
        "status": "success",
        "conversation_id": str(conversation.id),
        "response_length": total_length,
    }


async def process_slack_mention(
    team_id: str,
    channel_id: str,
    user_id: str,
    message_text: str,
    thread_ts: str,
) -> dict[str, Any]:
    """
    Process an @mention of the bot in a Slack channel.
    
    Similar to process_slack_dm but replies in a thread.
    
    Args:
        team_id: Slack workspace/team ID
        channel_id: Slack channel ID where mention occurred
        user_id: Slack user ID who mentioned the bot
        message_text: Message text (with @mention stripped)
        thread_ts: Thread timestamp to reply in
        
    Returns:
        Dict with status and conversation details
    """
    logger.info(
        "[slack_conversations] Processing @mention: team=%s, channel=%s, user=%s, thread=%s",
        team_id,
        channel_id,
        user_id,
        thread_ts,
    )
    
    # Find the organization for this Slack workspace
    organization_id = await find_organization_by_slack_team(team_id)
    if not organization_id:
        logger.warning("[slack_conversations] No organization found for team %s", team_id)
        return {"status": "error", "error": f"No organization found for team {team_id}"}
    
    connector = SlackConnector(organization_id=organization_id)

    # Show a reaction so the user knows the bot is working
    await connector.add_reaction(channel=channel_id, timestamp=thread_ts)

    # For channel mentions, use a conversation keyed by channel+thread
    # This allows threaded conversations to maintain context
    source_channel_id: str = f"{channel_id}:{thread_ts}"

    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=source_channel_id,
        slack_user_id=user_id,
    )

    # Process message through orchestrator, posting incrementally
    orchestrator = ChatOrchestrator(
        user_id=None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=None,
        workflow_context={"slack_channel_id": channel_id},
    )

    total_length: int = await _stream_and_post_responses(
        orchestrator=orchestrator,
        connector=connector,
        message_text=message_text,
        channel=channel_id,
        thread_ts=thread_ts,
    )

    # Remove the "thinking" reaction
    await connector.remove_reaction(channel=channel_id, timestamp=thread_ts)

    logger.info(
        "[slack_conversations] Posted thread response to %s (thread %s, %d chars)",
        channel_id,
        thread_ts,
        total_length,
    )
    return {
        "status": "success",
        "conversation_id": str(conversation.id),
        "response_length": total_length,
    }


async def process_slack_thread_reply(
    team_id: str,
    channel_id: str,
    user_id: str,
    message_text: str,
    thread_ts: str,
    event_ts: str,
) -> dict[str, Any]:
    """
    Process a thread reply in a channel where the bot is already participating.

    This handles the case where a user replies in a thread (without an
    @mention) that the bot previously responded in.  If no existing
    conversation is found for the thread, the message is silently ignored.

    Args:
        team_id: Slack workspace/team ID
        channel_id: Slack channel ID containing the thread
        user_id: Slack user ID who sent the reply
        message_text: The reply text
        thread_ts: Parent thread timestamp
        event_ts: Timestamp of the reply message itself (for reactions)

    Returns:
        Dict with status and conversation details
    """
    logger.info(
        "[slack_conversations] Processing thread reply: team=%s, channel=%s, user=%s, thread=%s",
        team_id,
        channel_id,
        user_id,
        thread_ts,
    )

    # Find the organization for this Slack workspace
    organization_id: str | None = await find_organization_by_slack_team(team_id)
    if not organization_id:
        logger.warning("[slack_conversations] No organization found for team %s", team_id)
        return {"status": "error", "error": f"No organization found for team {team_id}"}

    # Only respond if the bot already has a conversation in this thread
    conversation: Conversation | None = await find_thread_conversation(
        organization_id=organization_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    if conversation is None:
        logger.debug(
            "[slack_conversations] No existing conversation for thread %s:%s — ignoring",
            channel_id,
            thread_ts,
        )
        return {"status": "ignored", "reason": "bot not participating in thread"}

    connector = SlackConnector(organization_id=organization_id)

    # Show a reaction on the user's reply so they know the bot is working
    await connector.add_reaction(channel=channel_id, timestamp=event_ts)

    # Process message through orchestrator, posting incrementally
    orchestrator = ChatOrchestrator(
        user_id=None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=None,
        workflow_context={"slack_channel_id": channel_id},
    )

    total_length: int = await _stream_and_post_responses(
        orchestrator=orchestrator,
        connector=connector,
        message_text=message_text,
        channel=channel_id,
        thread_ts=thread_ts,
    )

    # Remove the "thinking" reaction
    await connector.remove_reaction(channel=channel_id, timestamp=event_ts)

    logger.info(
        "[slack_conversations] Posted thread reply to %s (thread %s, %d chars)",
        channel_id,
        thread_ts,
        total_length,
    )

    return {
        "status": "success",
        "conversation_id": str(conversation.id),
        "response_length": len(response_text),
    }
