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
import re
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.orchestrator import ChatOrchestrator
from connectors.slack import SlackConnector
from models.activity import Activity
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.slack_user_mapping import SlackUserMapping
from models.user import User
from services.nango import extract_connection_metadata, get_nango_client
from config import get_nango_integration_id

logger = logging.getLogger(__name__)


def _cannot_action_message() -> str:
    return (
        "I'm sorry, I can't help with that right now. "
        "Try connecting Slack to RevTops for your account."
    )


async def _post_cannot_action_response(
    connector: SlackConnector,
    channel: str,
    thread_ts: str | None = None,
) -> None:
    await connector.post_message(
        channel=channel,
        text=_cannot_action_message(),
        thread_ts=thread_ts,
    )


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


_EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _collect_emails_from_value(value: Any, emails: set[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        for match in _EMAIL_REGEX.findall(value):
            emails.add(match.strip().lower())
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_emails_from_value(item, emails)
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _collect_emails_from_value(item, emails)


def _extract_emails_from_payload(payload: dict[str, Any] | None) -> set[str]:
    emails: set[str] = set()
    if not payload:
        return emails
    _collect_emails_from_value(payload, emails)
    return emails


def _find_slack_user_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    if isinstance(payload.get("user"), dict):
        return payload["user"]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("user"), dict):
        return data["user"]
    if isinstance(data, dict):
        return data
    return payload


def _extract_slack_user_id_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    for key in ("id", "user_id", "slack_user_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def upsert_slack_user_mapping_from_nango_action(
    organization_id: str,
    user_id: UUID,
    action_result: dict[str, Any],
    match_source: str = "slack_nango_get_user_info",
) -> int:
    slack_user_payload = _find_slack_user_payload(action_result)
    slack_user_id = _extract_slack_user_id_from_payload(slack_user_payload) or _extract_slack_user_id_from_payload(
        action_result
    )
    if not slack_user_id:
        logger.warning(
            "[slack_conversations] Nango Slack user info missing user id org=%s user=%s keys=%s",
            organization_id,
            user_id,
            sorted(action_result.keys()),
        )
        return 0

    emails = _extract_emails_from_payload(slack_user_payload or action_result)
    slack_email = ",".join(sorted(emails)) if emails else None
    logger.info(
        "[slack_conversations] Upserting Slack mapping from Nango action org=%s user=%s slack_user=%s emails=%s",
        organization_id,
        user_id,
        slack_user_id,
        sorted(emails),
    )
    await _upsert_slack_user_mapping(
        organization_id=organization_id,
        user_id=user_id,
        slack_user_id=slack_user_id,
        slack_email=slack_email,
        match_source=match_source,
    )
    return 1


async def upsert_slack_user_mappings_from_metadata(
    organization_id: str,
    user_id: UUID,
    integration_metadata: dict[str, Any] | None,
) -> int:
    """Upsert Slack user mappings based on integration metadata for a user."""
    slack_user_ids = _extract_slack_user_ids(integration_metadata or {})
    if not slack_user_ids:
        logger.info(
            "[slack_conversations] No Slack user IDs found in integration metadata for org=%s user=%s",
            organization_id,
            user_id,
        )
        return 0

    created_count = 0
    for slack_user_id in sorted(slack_user_ids):
        slack_user = await _fetch_slack_user_info(
            organization_id=organization_id,
            slack_user_id=slack_user_id,
        )
        slack_email = _extract_slack_email(slack_user)
        await _upsert_slack_user_mapping(
            organization_id=organization_id,
            user_id=user_id,
            slack_user_id=slack_user_id,
            slack_email=slack_email,
            match_source="slack_integration_metadata",
        )
        created_count += 1

    logger.info(
        "[slack_conversations] Upserted %d Slack user mappings from integration metadata for org=%s user=%s",
        created_count,
        organization_id,
        user_id,
    )
    return created_count


async def refresh_slack_user_mappings_for_org(organization_id: str) -> int:
    """Refresh Slack user mappings for active Slack integrations in an org."""
    async with get_admin_session() as session:
        integrations_query = (
            select(Integration)
            .where(Integration.organization_id == UUID(organization_id))
            .where(Integration.provider == "slack")
            .where(Integration.is_active == True)
        )
        integrations_result = await session.execute(integrations_query)
        slack_integrations = integrations_result.scalars().all()

    if not slack_integrations:
        logger.info(
            "[slack_conversations] No active Slack integrations found for org=%s when refreshing mappings",
            organization_id,
        )
        return 0

    total_created = 0
    for integration in slack_integrations:
        await _hydrate_slack_integration_metadata(integration)
        target_user_id = integration.user_id or integration.connected_by_user_id
        if not target_user_id:
            logger.warning(
                "[slack_conversations] Slack integration %s has no user_id or connected_by_user_id; attempting resolution",
                integration.id,
            )
            resolved_user = await _resolve_user_for_slack_integration(
                organization_id=organization_id,
                integration=integration,
            )
            if resolved_user:
                target_user_id = resolved_user.id
                logger.info(
                    "[slack_conversations] Resolved integration %s to user %s for mapping refresh",
                    integration.id,
                    target_user_id,
                )
            else:
                logger.warning(
                    "[slack_conversations] Unable to resolve user for Slack integration %s; skipping mapping refresh",
                    integration.id,
                )
                continue
        created_count = await upsert_slack_user_mappings_from_metadata(
            organization_id=organization_id,
            user_id=target_user_id,
            integration_metadata=integration.extra_data or {},
        )
        total_created += created_count
        logger.debug(
            "[slack_conversations] Refreshed %d Slack user mappings for integration=%s user=%s",
            created_count,
            integration.id,
            target_user_id,
        )

    logger.info(
        "[slack_conversations] Refreshed %d Slack user mappings for org=%s",
        total_created,
        organization_id,
    )
    return total_created


async def refresh_slack_user_mappings_from_directory(
    organization_id: str,
    connector: SlackConnector,
) -> int:
    """Refresh Slack user mappings by matching Slack directory emails to RevTops users."""
    logger.info(
        "[slack_conversations] Starting Slack directory mapping refresh for org=%s",
        organization_id,
    )
    async with get_admin_session() as session:
        users_query = select(User).where(User.organization_id == UUID(organization_id))
        users_result = await session.execute(users_query)
        org_users = users_result.scalars().all()

    email_to_user: dict[str, User] = {}
    for user in org_users:
        if user.email:
            email_to_user[user.email.strip().lower()] = user

    logger.info(
        "[slack_conversations] Loaded %d org users with %d emails for org=%s",
        len(org_users),
        len(email_to_user),
        organization_id,
    )

    slack_users = await connector.get_users()
    logger.info(
        "[slack_conversations] Retrieved %d Slack users for org=%s",
        len(slack_users),
        organization_id,
    )

    mapped_count = 0
    for slack_user in slack_users:
        slack_user_id = slack_user.get("id")
        if not slack_user_id:
            logger.info(
                "[slack_conversations] Skipping Slack user without id org=%s payload_keys=%s",
                organization_id,
                sorted(slack_user.keys()),
            )
            continue

        if slack_user.get("deleted"):
            logger.info(
                "[slack_conversations] Skipping deleted Slack user=%s org=%s",
                slack_user_id,
                organization_id,
            )
            continue

        if slack_user.get("is_bot"):
            logger.info(
                "[slack_conversations] Skipping bot Slack user=%s org=%s",
                slack_user_id,
                organization_id,
            )
            continue

        logger.info(
            "[slack_conversations] Fetching Slack profile for user=%s org=%s",
            slack_user_id,
            organization_id,
        )
        slack_user_payload = slack_user
        if not slack_user.get("profile"):
            slack_user_payload = await _fetch_slack_user_info(
                organization_id=organization_id,
                slack_user_id=slack_user_id,
            )
        slack_email = _extract_slack_email(slack_user_payload)
        logger.info(
            "[slack_conversations] Slack user=%s org=%s has_email=%s",
            slack_user_id,
            organization_id,
            bool(slack_email),
        )

        if not slack_email:
            logger.info(
                "[slack_conversations] Skipping Slack user=%s org=%s due to missing email",
                slack_user_id,
                organization_id,
            )
            continue

        matched_user = email_to_user.get(slack_email)
        if not matched_user:
            logger.info(
                "[slack_conversations] No RevTops email match for Slack user=%s email=%s org=%s",
                slack_user_id,
                slack_email,
                organization_id,
            )
            continue

        logger.info(
            "[slack_conversations] Matched Slack user=%s email=%s to RevTops user=%s org=%s",
            slack_user_id,
            slack_email,
            matched_user.id,
            organization_id,
        )
        await _upsert_slack_user_mapping(
            organization_id=organization_id,
            user_id=matched_user.id,
            slack_user_id=slack_user_id,
            slack_email=slack_email,
            match_source="slack_directory_email",
        )
        mapped_count += 1

    logger.info(
        "[slack_conversations] Completed Slack directory mapping refresh for org=%s mapped=%d",
        organization_id,
        mapped_count,
    )
    return mapped_count


async def upsert_slack_user_mapping_from_current_profile(
    organization_id: str,
    connector: SlackConnector,
    integration: Integration | None,
) -> int:
    """Upsert Slack user mappings using the authenticated user's profile."""
    if not integration:
        logger.warning(
            "[slack_conversations] Missing Slack integration when mapping current profile for org=%s",
            organization_id,
        )
        return 0

    target_user_id = integration.user_id or integration.connected_by_user_id
    if not target_user_id:
        logger.warning(
            "[slack_conversations] Slack integration %s missing user_id for current profile mapping",
            integration.id,
        )
        return 0

    slack_user_ids = _extract_slack_user_ids(integration.extra_data or {})
    if not slack_user_ids:
        logger.warning(
            "[slack_conversations] Slack integration %s missing Slack user IDs for current profile mapping",
            integration.id,
        )
        return 0

    logger.info(
        "[slack_conversations] Fetching current Slack user profile for integration=%s org=%s",
        integration.id,
        organization_id,
    )
    profile = await connector.get_current_user_profile()
    if not profile:
        logger.warning(
            "[slack_conversations] Slack profile lookup returned empty profile for integration=%s",
            integration.id,
        )
        return 0

    slack_email = (profile.get("email") or "").strip().lower() or None
    created_count = 0
    for slack_user_id in sorted(slack_user_ids):
        await _upsert_slack_user_mapping(
            organization_id=organization_id,
            user_id=target_user_id,
            slack_user_id=slack_user_id,
            slack_email=slack_email,
            match_source="slack_current_user_profile",
        )
        created_count += 1

    logger.info(
        "[slack_conversations] Upserted %d Slack user mappings from current profile for integration=%s",
        created_count,
        integration.id,
    )
    return created_count


async def _hydrate_slack_integration_metadata(integration: Integration) -> None:
    extra_data = integration.extra_data or {}
    slack_user_ids = _extract_slack_user_ids(extra_data)
    if slack_user_ids:
        return

    if not integration.nango_connection_id:
        logger.info(
            "[slack_conversations] Slack integration %s missing metadata and nango_connection_id",
            integration.id,
        )
        return

    try:
        nango = get_nango_client()
        integration_id = get_nango_integration_id("slack")
        connection = await nango.get_connection(integration_id, integration.nango_connection_id)
        connection_metadata = extract_connection_metadata(connection) or {}
        if not connection_metadata:
            logger.info(
                "[slack_conversations] Slack integration %s has no metadata in Nango connection keys=%s",
                integration.id,
                sorted(connection.keys()),
            )
            return

        slack_user_ids = _extract_slack_user_ids(connection_metadata)
        if not slack_user_ids:
            logger.info(
                "[slack_conversations] Slack integration %s metadata missing Slack user IDs keys=%s",
                integration.id,
                sorted(connection_metadata.keys()),
            )
        await _update_integration_metadata(
            integration_id=integration.id,
            metadata=connection_metadata,
        )
        integration.extra_data = connection_metadata
        logger.info(
            "[slack_conversations] Refreshed Slack integration %s metadata from Nango keys=%s",
            integration.id,
            sorted(connection_metadata.keys()),
        )
    except Exception as exc:
        logger.warning(
            "[slack_conversations] Failed to hydrate Slack integration %s metadata from Nango: %s",
            integration.id,
            exc,
            exc_info=True,
        )


async def _update_integration_metadata(
    integration_id: UUID,
    metadata: dict[str, Any],
) -> None:
    async with get_admin_session() as session:
        stmt = (
            update(Integration)
            .where(Integration.id == integration_id)
            .values(
                extra_data=metadata,
                updated_at=datetime.utcnow(),
            )
        )
        await session.execute(stmt)
        await session.commit()


async def _resolve_user_for_slack_integration(
    organization_id: str,
    integration: Integration,
) -> User | None:
    extra_data = integration.extra_data or {}
    slack_user_ids = sorted(_extract_slack_user_ids(extra_data))
    if not slack_user_ids:
        logger.info(
            "[slack_conversations] Slack integration %s has no Slack user IDs in metadata; cannot resolve user",
            integration.id,
        )
        return None

    logger.info(
        "[slack_conversations] Attempting to resolve Slack integration %s user via Slack IDs=%s",
        integration.id,
        slack_user_ids,
    )
    for slack_user_id in slack_user_ids:
        resolved_user = await resolve_revtops_user_for_slack_actor(
            organization_id=organization_id,
            slack_user_id=slack_user_id,
        )
        if resolved_user:
            if not integration.connected_by_user_id:
                await _update_integration_connected_user(
                    integration_id=integration.id,
                    user_id=resolved_user.id,
                )
            return resolved_user

    logger.info(
        "[slack_conversations] No RevTops user resolved for Slack integration %s via Slack IDs=%s",
        integration.id,
        slack_user_ids,
    )
    return None


async def _update_integration_connected_user(
    integration_id: UUID,
    user_id: UUID,
) -> None:
    try:
        async with get_admin_session() as session:
            stmt = (
                update(Integration)
                .where(Integration.id == integration_id)
                .values(
                    connected_by_user_id=user_id,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.execute(stmt)
            await session.commit()
            logger.info(
                "[slack_conversations] Updated Slack integration %s connected_by_user_id=%s",
                integration_id,
                user_id,
            )
    except Exception as exc:
        logger.warning(
            "[slack_conversations] Failed to update Slack integration %s connected_by_user_id=%s: %s",
            integration_id,
            user_id,
            exc,
            exc_info=True,
        )


def _normalize_name(value: str | None) -> str:
    """Normalize a person name for case-insensitive equality matching."""
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


async def get_slack_user_ids_for_revtops_user(
    organization_id: str,
    user_id: str,
) -> set[str]:
    """Return Slack user IDs associated with a RevTops user in this org."""
    user_uuid = UUID(user_id)
    async with get_admin_session() as session:
        integrations_query = (
            select(Integration)
            .where(Integration.organization_id == UUID(organization_id))
            .where(Integration.provider == "slack")
            .where(Integration.is_active == True)
        )
        integrations_result = await session.execute(integrations_query)
        slack_integrations = integrations_result.scalars().all()
        mappings_query = (
            select(SlackUserMapping)
            .where(SlackUserMapping.organization_id == UUID(organization_id))
            .where(SlackUserMapping.user_id == user_uuid)
        )
        mappings_result = await session.execute(mappings_query)
        slack_mappings = mappings_result.scalars().all()

    slack_user_ids: set[str] = set()
    for integration in slack_integrations:
        if integration.user_id != user_uuid and integration.connected_by_user_id != user_uuid:
            continue
        slack_user_ids.update(_extract_slack_user_ids(integration.extra_data or {}))
    for mapping in slack_mappings:
        if mapping.slack_user_id:
            slack_user_ids.add(mapping.slack_user_id)

    logger.info(
        "[slack_conversations] Resolved %d Slack user IDs for org=%s user=%s (mappings=%d)",
        len(slack_user_ids),
        organization_id,
        user_id,
        len(slack_mappings),
    )
    return slack_user_ids


async def _upsert_slack_user_mapping(
    organization_id: str,
    user_id: UUID,
    slack_user_id: str,
    slack_email: str | None,
    match_source: str,
) -> None:
    now = datetime.utcnow()
    try:
        logger.info(
            "[slack_conversations] Attempting Slack user mapping upsert org=%s user=%s slack_user=%s email=%s source=%s",
            organization_id,
            user_id,
            slack_user_id,
            slack_email,
            match_source,
        )
        async with get_admin_session() as session:
            stmt = pg_insert(SlackUserMapping).values(
                id=uuid.uuid4(),
                organization_id=UUID(organization_id),
                user_id=user_id,
                slack_user_id=slack_user_id,
                slack_email=slack_email,
                match_source=match_source,
                created_at=now,
                updated_at=now,
            ).on_conflict_do_update(
                index_elements=["organization_id", "user_id", "slack_user_id"],
                set_={
                    "slack_email": slack_email,
                    "match_source": match_source,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)
            await session.commit()
            logger.info(
                "[slack_conversations] Upserted Slack user mapping org=%s user=%s slack_user=%s source=%s",
                organization_id,
                user_id,
                slack_user_id,
                match_source,
            )
    except Exception as exc:
        logger.warning(
            "[slack_conversations] Failed to upsert Slack user mapping org=%s user=%s slack_user=%s: %s",
            organization_id,
            user_id,
            slack_user_id,
            exc,
            exc_info=True,
        )


async def _fetch_slack_user_info(
    organization_id: str,
    slack_user_id: str,
) -> dict[str, Any] | None:
    try:
        logger.info(
            "[slack_conversations] Fetching Slack user info for user=%s org=%s",
            slack_user_id,
            organization_id,
        )
        connector = SlackConnector(organization_id=organization_id)
        return await connector.get_user_info(slack_user_id)
    except Exception as exc:
        logger.warning(
            "[slack_conversations] Failed Slack users.info lookup for user=%s org=%s: %s",
            slack_user_id,
            organization_id,
            exc,
            exc_info=True,
        )
        return None


def _extract_slack_email(slack_user: dict[str, Any] | None) -> str | None:
    if not slack_user:
        return None
    profile = slack_user.get("profile", {})
    slack_email = (profile.get("email") or "").strip().lower()
    return slack_email or None


def _extract_slack_display_name(slack_user: dict[str, Any] | None) -> str | None:
    if not slack_user:
        return None
    profile = slack_user.get("profile", {})
    display_name = (
        profile.get("display_name")
        or profile.get("real_name")
        or slack_user.get("real_name")
        or slack_user.get("name")
        or ""
    ).strip()
    return display_name or None


async def resolve_revtops_user_for_slack_actor(
    organization_id: str,
    slack_user_id: str,
    slack_user: dict[str, Any] | None = None,
) -> User | None:
    """Resolve the RevTops user linked to a Slack actor in this organization."""

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

        mappings_query = (
            select(SlackUserMapping)
            .where(SlackUserMapping.organization_id == UUID(organization_id))
            .where(SlackUserMapping.slack_user_id == slack_user_id)
        )
        mappings_result = await session.execute(mappings_query)
        existing_mappings = mappings_result.scalars().all()

    if existing_mappings:
        latest_mapping = max(
            existing_mappings,
            key=lambda mapping: getattr(mapping, "updated_at", datetime.min),
        )
        if len(existing_mappings) > 1:
            logger.info(
                "[slack_conversations] Multiple Slack mappings found for user=%s (count=%d); using latest user=%s",
                slack_user_id,
                len(existing_mappings),
                latest_mapping.user_id,
            )
        for user in org_users:
            if user.id == latest_mapping.user_id:
                logger.info(
                    "[slack_conversations] Resolved Slack user=%s via stored mapping to RevTops user=%s",
                    slack_user_id,
                    user.id,
                )
                return user
        logger.info(
            "[slack_conversations] Stored mapping for Slack user=%s references missing user=%s",
            slack_user_id,
            latest_mapping.user_id,
        )

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
                    await _upsert_slack_user_mapping(
                        organization_id=organization_id,
                        user_id=user.id,
                        slack_user_id=slack_user_id,
                        slack_email=None,
                        match_source="slack_integration",
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
        slack_user = slack_user or await _fetch_slack_user_info(
            organization_id=organization_id,
            slack_user_id=slack_user_id,
        )
        if not slack_user:
            return None
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
            "[slack_conversations] Failed Slack user resolution for user=%s org=%s: %s",
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
                await _upsert_slack_user_mapping(
                    organization_id=organization_id,
                    user_id=user.id,
                    slack_user_id=slack_user_id,
                    slack_email=slack_email,
                    match_source="email",
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
    slack_user_name: str | None = None,
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
            if slack_user_name and (not conversation.title or conversation.title == "Slack DM"):
                conversation.title = f"Slack DM - {slack_user_name}"
                await session.commit()
                logger.info(
                    "[slack_conversations] Updated Slack conversation %s title to %s",
                    conversation.id,
                    conversation.title,
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
            title=f"Slack DM - {slack_user_name}" if slack_user_name else "Slack DM",
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
    slack_user = await _fetch_slack_user_info(
        organization_id=organization_id,
        slack_user_id=user_id,
    )
    slack_email = _extract_slack_email(slack_user)

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
                    "sender_slack_id": user_id,
                    "sender_email": slack_email,
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
        current_text += f"\n{_cannot_action_message()}"

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
    slack_user = await _fetch_slack_user_info(
        organization_id=organization_id,
        slack_user_id=user_id,
    )
    linked_user = await resolve_revtops_user_for_slack_actor(
        organization_id=organization_id,
        slack_user_id=user_id,
        slack_user=slack_user,
    )
    slack_user_name = _extract_slack_display_name(slack_user)
    slack_user_email = _extract_slack_email(slack_user)
    if not linked_user:
        logger.warning(
            "[slack_conversations] No linked RevTops user for Slack actor=%s org=%s",
            user_id,
            organization_id,
        )
        await _post_cannot_action_response(
            connector=connector,
            channel=channel_id,
        )
        await connector.remove_reaction(channel=channel_id, timestamp=event_ts)
        return {"status": "error", "error": "No linked RevTops user for Slack actor"}

    # Find or create conversation
    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=channel_id,
        slack_user_id=user_id,
        revtops_user_id=str(linked_user.id) if linked_user else None,
        slack_user_name=slack_user_name,
    )

    # Process message through orchestrator
    orchestrator = ChatOrchestrator(
        user_id=str(linked_user.id) if linked_user else None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=linked_user.email if linked_user else None,
        source_user_id=user_id,
        source_user_email=slack_user_email,
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
    slack_user = await _fetch_slack_user_info(
        organization_id=organization_id,
        slack_user_id=user_id,
    )

    # For channel mentions, use a conversation keyed by channel+thread
    # This allows threaded conversations to maintain context
    source_channel_id: str = f"{channel_id}:{thread_ts}"
    
    linked_user = await resolve_revtops_user_for_slack_actor(
        organization_id=organization_id,
        slack_user_id=user_id,
        slack_user=slack_user,
    )
    slack_user_name = _extract_slack_display_name(slack_user)
    slack_user_email = _extract_slack_email(slack_user)
    if not linked_user:
        logger.warning(
            "[slack_conversations] No linked RevTops user for Slack actor=%s org=%s",
            user_id,
            organization_id,
        )
        await _post_cannot_action_response(
            connector=connector,
            channel=channel_id,
            thread_ts=thread_ts,
        )
        await connector.remove_reaction(channel=channel_id, timestamp=thread_ts)
        return {"status": "error", "error": "No linked RevTops user for Slack actor"}
    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=source_channel_id,
        slack_user_id=user_id,
        revtops_user_id=str(linked_user.id) if linked_user else None,
        slack_user_name=slack_user_name,
    )

    # Process message through orchestrator
    orchestrator = ChatOrchestrator(
        user_id=str(linked_user.id) if linked_user else None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=linked_user.email if linked_user else None,
        source_user_id=user_id,
        source_user_email=slack_user_email,
        workflow_context=None,
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
    slack_user = await _fetch_slack_user_info(
        organization_id=organization_id,
        slack_user_id=user_id,
    )
    slack_user_email = _extract_slack_email(slack_user)
    await resolve_revtops_user_for_slack_actor(
        organization_id=organization_id,
        slack_user_id=user_id,
        slack_user=slack_user,
    )

    # Process message through orchestrator, posting incrementally
    orchestrator = ChatOrchestrator(
        user_id=None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=None,
        source_user_id=user_id,
        source_user_email=slack_user_email,
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
        "response_length": total_length,
    }
