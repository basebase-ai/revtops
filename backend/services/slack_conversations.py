"""
Slack DM conversation service.

Handles processing incoming Slack DMs and routing them through the agent orchestrator.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select

from agents.orchestrator import ChatOrchestrator
from connectors.slack import SlackConnector
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.user import User

logger = logging.getLogger(__name__)


async def find_organization_by_slack_team(team_id: str) -> str | None:
    """
    Find the organization ID for a Slack team/workspace.

    Matches the incoming team_id against metadata on active Slack integrations.

    Args:
        team_id: Slack workspace/team ID (e.g., "T04ABCDEF")

    Returns:
        Organization ID string or None if not found
    """
    async with get_admin_session() as session:
        query = (
            select(Integration)
            .where(Integration.provider == "slack")
            .where(Integration.is_active == True)
        )
        result = await session.execute(query)
        integrations = result.scalars().all()

        for integration in integrations:
            extra_data = integration.extra_data or {}
            if extra_data.get("team_id") == team_id:
                logger.info(
                    "[slack_conversations] Matched Slack team %s to org %s via integration metadata",
                    team_id,
                    integration.organization_id,
                )
                return str(integration.organization_id)

        if len(integrations) == 1:
            logger.warning(
                "[slack_conversations] No team_id metadata match; using the only active Slack integration org=%s for team=%s",
                integrations[0].organization_id,
                team_id,
            )
            return str(integrations[0].organization_id)

    logger.warning("[slack_conversations] No Slack integration found for team=%s", team_id)
    return None


async def resolve_revtops_user_for_slack_actor(
    organization_id: str,
    slack_user_id: str,
) -> User | None:
    """Resolve the RevTops user linked to a Slack actor in this organization."""
    def _normalize_name(value: str | None) -> str:
        """Normalize a person name for case-insensitive equality matching."""
        if not value:
            return ""
        return " ".join(value.strip().lower().split())

    def _extract_slack_user_ids(extra_data: dict[str, Any]) -> set[str]:
        """Extract possible Slack user IDs from integration metadata."""
        candidates: set[str] = set()
        if not extra_data:
            return candidates

        for key in ("authed_user_id", "user_id", "installer_user_id"):
            value = extra_data.get(key)
            if isinstance(value, str) and value:
                candidates.add(value)

        authed_user = extra_data.get("authed_user")
        if isinstance(authed_user, dict):
            authed_user_id = authed_user.get("id") or authed_user.get("user_id")
            if isinstance(authed_user_id, str) and authed_user_id:
                candidates.add(authed_user_id)

        return candidates

    async with get_admin_session() as session:
        users_query = select(User).where(User.organization_id == UUID(organization_id))
        users_result = await session.execute(users_query)
        org_users = users_result.scalars().all()

        # "Connected their Slack" can be represented by either user-scoped Slack
        # integrations (user_id) or organization-scoped Slack integrations that were
        # authorized by a specific user (connected_by_user_id).
        integrations_query = (
            select(Integration)
            .where(Integration.organization_id == UUID(organization_id))
            .where(Integration.provider == "slack")
            .where(Integration.is_active == True)
        )
        integrations_result = await session.execute(integrations_query)
        slack_integrations = integrations_result.scalars().all()

    for integration in slack_integrations:
        extra_data = integration.extra_data or {}
        slack_user_ids = _extract_slack_user_ids(extra_data)
        if slack_user_id in slack_user_ids:
            target_user_id = integration.user_id or integration.connected_by_user_id
            if not target_user_id:
                logger.info(
                    "[slack_conversations] Slack metadata matched user=%s but no linked user_id on integration %s",
                    slack_user_id,
                    integration.id,
                )
                continue

            for user in org_users:
                if user.id == target_user_id:
                    logger.info(
                        "[slack_conversations] Matched Slack user=%s via integration metadata to RevTops user=%s",
                        slack_user_id,
                        user.id,
                    )
                    return user

            logger.info(
                "[slack_conversations] Slack metadata matched user=%s but no org user found for %s",
                slack_user_id,
                target_user_id,
            )

    users_with_connected_slack: set[UUID] = {
        integration.user_id
        for integration in slack_integrations
        if integration.user_id is not None
    }
    users_with_connected_slack.update(
        integration.connected_by_user_id
        for integration in slack_integrations
        if integration.connected_by_user_id is not None
    )

    if not org_users:
        logger.info(
            "[slack_conversations] No users found for org=%s when resolving Slack user=%s",
            organization_id,
            slack_user_id,
        )
        return None

    try:
        connector = SlackConnector(organization_id=organization_id)
        slack_user = await connector.get_user_info(slack_user_id)
        profile = slack_user.get("profile", {})
        slack_email = (profile.get("email") or "").strip().lower()
        slack_names = {
            _normalize_name(slack_user.get("name")),
            _normalize_name(slack_user.get("real_name")),
            _normalize_name(profile.get("display_name")),
            _normalize_name(profile.get("real_name")),
            _normalize_name(profile.get("display_name_normalized")),
            _normalize_name(profile.get("real_name_normalized")),
        }
        slack_names.discard("")

        logger.info(
            "[slack_conversations] Slack user resolution lookup user=%s has_email=%s candidate_names=%s users_with_connected_slack=%d",
            slack_user_id,
            bool(slack_email),
            sorted(slack_names),
            len(users_with_connected_slack),
        )
    except Exception as exc:
        logger.warning(
            "[slack_conversations] Failed Slack users.info lookup for user=%s org=%s: %s",
            slack_user_id,
            organization_id,
            exc,
            exc_info=True,
        )
        return None

    # 1) email matching
    if slack_email:
        for user in org_users:
            if user.email and user.email.strip().lower() == slack_email:
                logger.info(
                    "[slack_conversations] Matched Slack user=%s email=%s to RevTops user=%s",
                    slack_user_id,
                    slack_email,
                    user.id,
                )
                return user

        logger.info(
            "[slack_conversations] No email match for Slack user=%s email=%s org=%s",
            slack_user_id,
            slack_email,
            organization_id,
        )

    # 2) slack name matching for users who have connected Slack
    if slack_names and users_with_connected_slack:
        for user in org_users:
            if user.id not in users_with_connected_slack:
                continue

            user_name = _normalize_name(user.name)
            if user_name and user_name in slack_names:
                logger.info(
                    "[slack_conversations] Matched Slack user=%s by name=%s to connected RevTops user=%s",
                    slack_user_id,
                    user_name,
                    user.id,
                )
                return user

        logger.info(
            "[slack_conversations] No connected Slack-name match for Slack user=%s org=%s candidate_names=%s",
            slack_user_id,
            organization_id,
            sorted(slack_names),
        )

    logger.info(
        "[slack_conversations] Failed to resolve RevTops user for Slack actor user=%s org=%s",
        slack_user_id,
        organization_id,
    )
    return None


async def find_or_create_conversation(
    organization_id: str,
    slack_channel_id: str,
    slack_user_id: str,
    revtops_user_id: str | None,
) -> Conversation:
    """
    Find an existing Slack conversation or create a new one.
    
    Conversations are keyed by (source='slack', source_channel_id).
    
    Args:
        organization_id: The organization this conversation belongs to
        slack_channel_id: Slack DM channel ID
        slack_user_id: Slack user ID who initiated the conversation
        revtops_user_id: Linked RevTops user UUID string if available
        
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
            if revtops_user_id and conversation.user_id is None:
                conversation.user_id = UUID(revtops_user_id)
                await session.commit()
                logger.info(
                    "[slack_conversations] Linked existing conversation %s to user %s",
                    conversation.id,
                    revtops_user_id,
                )

            logger.info(
                "[slack_conversations] Found existing conversation %s for channel %s",
                conversation.id,
                slack_channel_id
            )
            return conversation
        
        # Create new conversation for this Slack DM
        conversation = Conversation(
            organization_id=UUID(organization_id),
            user_id=UUID(revtops_user_id) if revtops_user_id else None,
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
            "[slack_conversations] Created new conversation %s for channel %s user_id=%s source_user_id=%s",
            conversation.id,
            slack_channel_id,
            conversation.user_id,
            slack_user_id,
        )
        return conversation


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
    
    linked_user = await resolve_revtops_user_for_slack_actor(
        organization_id=organization_id,
        slack_user_id=user_id,
    )

    # Find or create conversation
    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=channel_id,
        slack_user_id=user_id,
        revtops_user_id=str(linked_user.id) if linked_user else None,
    )

    # Process message through orchestrator
    orchestrator = ChatOrchestrator(
        user_id=str(linked_user.id) if linked_user else None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=linked_user.email if linked_user else None,
        workflow_context=None,
    )
    
    # Collect the full response (don't stream to Slack)
    response_text = ""
    try:
        async for chunk in orchestrator.process_message(message_text):
            # Skip JSON chunks (tool calls, etc.) - just collect text
            if not chunk.startswith("{"):
                response_text += chunk
    except Exception as e:
        logger.error("[slack_conversations] Error processing message: %s", e, exc_info=True)
        response_text = f"Sorry, I encountered an error processing your message: {str(e)}"
    
    # Post response back to Slack (connector auto-converts markdown to mrkdwn)
    if response_text.strip():
        try:
            connector = SlackConnector(organization_id=organization_id)
            await connector.post_message(
                channel=channel_id,
                text=response_text.strip(),
            )
            logger.info(
                "[slack_conversations] Posted response to channel %s (%d chars)",
                channel_id,
                len(response_text)
            )
        except Exception as e:
            logger.error("[slack_conversations] Error posting to Slack: %s", e, exc_info=True)
            return {
                "status": "error",
                "error": f"Failed to post response to Slack: {str(e)}"
            }
    
    return {
        "status": "success",
        "conversation_id": str(conversation.id),
        "response_length": len(response_text),
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
    
    # For channel mentions, use a conversation keyed by channel+thread
    # This allows threaded conversations to maintain context
    source_channel_id = f"{channel_id}:{thread_ts}"
    
    linked_user = await resolve_revtops_user_for_slack_actor(
        organization_id=organization_id,
        slack_user_id=user_id,
    )

    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=source_channel_id,
        slack_user_id=user_id,
        revtops_user_id=str(linked_user.id) if linked_user else None,
    )

    # Process message through orchestrator
    orchestrator = ChatOrchestrator(
        user_id=str(linked_user.id) if linked_user else None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=linked_user.email if linked_user else None,
        workflow_context=None,
    )
    
    # Collect the full response
    response_text = ""
    try:
        async for chunk in orchestrator.process_message(message_text):
            if not chunk.startswith("{"):
                response_text += chunk
    except Exception as e:
        logger.error("[slack_conversations] Error processing mention: %s", e, exc_info=True)
        response_text = f"Sorry, I encountered an error: {str(e)}"
    
    # Post response back to Slack in the thread
    if response_text.strip():
        try:
            connector = SlackConnector(organization_id=organization_id)
            await connector.post_message(
                channel=channel_id,
                text=response_text.strip(),
                thread_ts=thread_ts,  # Reply in thread
            )
            logger.info(
                "[slack_conversations] Posted thread response to %s (thread %s, %d chars)",
                channel_id,
                thread_ts,
                len(response_text)
            )
        except Exception as e:
            logger.error("[slack_conversations] Error posting to Slack: %s", e, exc_info=True)
            return {"status": "error", "error": f"Failed to post response: {str(e)}"}
    
    return {
        "status": "success",
        "conversation_id": str(conversation.id),
        "response_length": len(response_text),
    }
