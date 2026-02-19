"""
Slack conversation service.

Handles processing incoming Slack messages (DMs, @mentions, thread replies)
and routing them through the agent orchestrator.  Also persists inbound
channel messages as Activity rows for real-time queryability.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agents.orchestrator import ChatOrchestrator
from connectors.slack import SlackConnector
from models.activity import Activity
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.org_member import OrgMember
from models.slack_user_mapping import SlackUserMapping
from models.user import User
from services.nango import extract_connection_metadata, get_nango_client
from config import get_nango_integration_id

logger = logging.getLogger(__name__)
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


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
    Falls back to calling Slack's ``auth.test`` to resolve and backfill the
    team_id when it is missing from ``extra_data``.

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
        integrations: list[Integration] = list(result.scalars().all())

        # --- Fast path: match on stored team_id in extra_data ---
        for integration in integrations:
            extra_data: dict[str, Any] = integration.extra_data or {}
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

        # --- Slow path: resolve team_id via auth.test for integrations missing it ---
        integrations_missing_team_id: list[Integration] = [
            i for i in integrations if not (i.extra_data or {}).get("team_id")
        ]
        if integrations_missing_team_id:
            logger.info(
                "[slack_conversations] %d Slack integration(s) missing team_id in extra_data; resolving via auth.test",
                len(integrations_missing_team_id),
            )
            for integration in integrations_missing_team_id:
                resolved_team_id: str | None = await _resolve_team_id_via_auth_test(integration)
                if resolved_team_id is None:
                    continue
                # Backfill the team_id so future lookups use the fast path
                merged_extra: dict[str, Any] = dict(integration.extra_data or {})
                merged_extra["team_id"] = resolved_team_id
                await _update_integration_metadata(integration.id, merged_extra)
                logger.info(
                    "[slack_conversations] Backfilled team_id=%s for Slack integration %s (org %s)",
                    resolved_team_id,
                    integration.id,
                    integration.organization_id,
                )
                if resolved_team_id == team_id:
                    logger.info(
                        "[slack_conversations] Matched Slack team %s to org %s via auth.test",
                        team_id,
                        integration.organization_id,
                    )
                    return str(integration.organization_id)

    logger.warning("[slack_conversations] No Slack integration found for team=%s", team_id)
    return None


async def _resolve_team_id_via_auth_test(integration: Integration) -> str | None:
    """Call Slack ``auth.test`` to discover the team_id for an integration.

    Returns the team_id string, or None on failure.
    """
    import httpx

    nango_connection_id: str | None = integration.nango_connection_id
    if not nango_connection_id:
        logger.warning(
            "[slack_conversations] Cannot resolve team_id: integration %s has no nango_connection_id",
            integration.id,
        )
        return None

    try:
        nango = get_nango_client()
        nango_integration_id: str = get_nango_integration_id("slack")
        token: str = await nango.get_token(nango_integration_id, nango_connection_id)
    except Exception as exc:
        logger.warning(
            "[slack_conversations] Failed to get Slack token for integration %s: %s",
            integration.id,
            exc,
        )
        return None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            if not data.get("ok"):
                logger.warning(
                    "[slack_conversations] auth.test failed for integration %s: %s",
                    integration.id,
                    data.get("error", "unknown"),
                )
                return None
            resolved: str | None = data.get("team_id")
            if resolved:
                logger.info(
                    "[slack_conversations] auth.test returned team_id=%s for integration %s",
                    resolved,
                    integration.id,
                )
            return resolved
    except Exception as exc:
        logger.warning(
            "[slack_conversations] auth.test request failed for integration %s: %s",
            integration.id,
            exc,
        )
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
        # Include users from org_members (multi-org support).
        org_uuid: UUID = UUID(organization_id)
        membership_subq = (
            select(OrgMember.user_id)
            .where(OrgMember.organization_id == org_uuid)
            .where(OrgMember.status == "active")
        )
        users_query = select(User).where(
            or_(
                User.organization_id == org_uuid,
                User.id.in_(membership_subq),
            )
        )
        users_result = await session.execute(users_query)
        org_users: list[User] = list(users_result.scalars().all())

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

        matched_user = None
        if slack_email:
            matched_user = email_to_user.get(slack_email)

        if matched_user:
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
                revtops_email=matched_user.email,
            )
            mapped_count += 1
        else:
            logger.info(
                "[slack_conversations] Storing unmapped Slack user=%s email=%s org=%s",
                slack_user_id,
                slack_email,
                organization_id,
            )
            await _upsert_slack_user_mapping(
                organization_id=organization_id,
                user_id=None,
                slack_user_id=slack_user_id,
                slack_email=slack_email,
                match_source="slack_directory_unmapped",
            )

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

    if not connector.user_id:
        logger.warning(
            "[slack_conversations] Slack integration %s missing current user id for profile mapping",
            integration.id,
        )
        return 0
    target_user_id = UUID(connector.user_id)

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
        mappings_query = (
            select(SlackUserMapping)
            .where(SlackUserMapping.organization_id == UUID(organization_id))
            .where(SlackUserMapping.source == "slack")
            .where(SlackUserMapping.user_id == user_uuid)
        )
        mappings_result = await session.execute(mappings_query)
        slack_mappings = mappings_result.scalars().all()
        integrations_query = (
            select(Integration)
            .where(Integration.organization_id == UUID(organization_id))
            .where(Integration.provider == "slack")
            .where(Integration.is_active == True)
        )
        integrations_result = await session.execute(integrations_query)
        slack_integrations = integrations_result.scalars().all()

    slack_user_ids: set[str] = set()
    for mapping in slack_mappings:
        if mapping.external_userid:
            slack_user_ids.add(mapping.external_userid)

    logger.info(
        "[slack_conversations] Resolved %d Slack user IDs for org=%s user=%s (mappings=%d)",
        len(slack_user_ids),
        organization_id,
        user_id,
        len(slack_mappings),
    )
    if not slack_user_ids and slack_integrations:
        logger.info(
            "[slack_conversations] No Slack user mappings found for org=%s user=%s with %d active Slack integrations",
            organization_id,
            user_id,
            len(slack_integrations),
        )
    return slack_user_ids


async def _upsert_slack_user_mapping(
    organization_id: str,
    user_id: UUID | None,
    slack_user_id: str | None,
    slack_email: str | None,
    match_source: str,
    revtops_email: str | None = None,
) -> None:
    now = datetime.utcnow()
    if not slack_user_id:
        logger.warning(
            "[slack_conversations] Skipping Slack user mapping upsert org=%s user=%s due to missing slack_user_id",
            organization_id,
            user_id,
        )
        return
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
            resolved_revtops_email = revtops_email
            if user_id and not resolved_revtops_email:
                result = await session.execute(
                    select(User.email).where(User.id == user_id)
                )
                user_email = result.scalar_one_or_none()
                if isinstance(user_email, str) and user_email.strip():
                    resolved_revtops_email = user_email.strip().lower()

            if user_id:
                existing_result = await session.execute(
                    select(SlackUserMapping)
                    .where(SlackUserMapping.organization_id == UUID(organization_id))
                    .where(SlackUserMapping.source == "slack")
                    .where(SlackUserMapping.external_userid == slack_user_id)
                    .where(SlackUserMapping.user_id.is_(None))
                    .limit(1)
                )
                existing_mapping = existing_result.scalar_one_or_none()
                if existing_mapping:
                    existing_mapping.user_id = user_id
                    existing_mapping.revtops_email = resolved_revtops_email
                    existing_mapping.external_email = slack_email
                    existing_mapping.source = "slack"
                    existing_mapping.match_source = match_source
                    existing_mapping.updated_at = now
                    await session.commit()
                    logger.info(
                        "[slack_conversations] Promoted Slack user mapping org=%s slack_user=%s to user=%s source=%s",
                        organization_id,
                        slack_user_id,
                        user_id,
                        match_source,
                    )
                    return

                stmt = pg_insert(SlackUserMapping).values(
                    id=uuid.uuid4(),
                    organization_id=UUID(organization_id),
                    user_id=user_id,
                    revtops_email=resolved_revtops_email,
                    external_userid=slack_user_id,
                    external_email=slack_email,
                    source="slack",
                    match_source=match_source,
                    created_at=now,
                    updated_at=now,
                ).on_conflict_do_update(
                    index_elements=["organization_id", "user_id", "external_userid"],
                    set_={
                        "revtops_email": resolved_revtops_email,
                        "external_email": slack_email,
                        "source": "slack",
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
                return

            # Check if ANY row already exists for this identity (mapped or unmapped)
            any_existing_result = await session.execute(
                select(SlackUserMapping)
                .where(SlackUserMapping.organization_id == UUID(organization_id))
                .where(SlackUserMapping.source == "slack")
                .where(SlackUserMapping.external_userid == slack_user_id)
                .limit(1)
            )
            any_existing: SlackUserMapping | None = any_existing_result.scalar_one_or_none()
            if any_existing:
                # A row exists — only update if it's still unmapped
                if any_existing.user_id is None:
                    any_existing.external_email = slack_email
                    any_existing.source = "slack"
                    any_existing.match_source = match_source
                    any_existing.updated_at = now
                    await session.commit()
                    logger.info(
                        "[slack_conversations] Updated unmapped Slack user mapping org=%s slack_user=%s source=%s",
                        organization_id,
                        slack_user_id,
                        match_source,
                    )
                else:
                    # Already mapped to a user — skip, don't create a duplicate
                    logger.info(
                        "[slack_conversations] Skipping unmapped upsert — Slack user already mapped org=%s slack_user=%s user=%s",
                        organization_id,
                        slack_user_id,
                        any_existing.user_id,
                    )
                return

            mapping = SlackUserMapping(
                id=uuid.uuid4(),
                organization_id=UUID(organization_id),
                user_id=None,
                revtops_email=resolved_revtops_email,
                external_userid=slack_user_id,
                external_email=slack_email,
                source="slack",
                match_source=match_source,
                created_at=now,
                updated_at=now,
            )
            session.add(mapping)
            await session.commit()
            logger.info(
                "[slack_conversations] Created unmapped Slack user mapping org=%s slack_user=%s source=%s",
                organization_id,
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


async def upsert_slack_user_mapping_for_user(
    organization_id: str,
    user_id: UUID,
    slack_user_id: str,
    slack_email: str | None,
    match_source: str,
) -> None:
    """Public helper to upsert a Slack mapping for a specific user."""
    await _upsert_slack_user_mapping(
        organization_id=organization_id,
        user_id=user_id,
        slack_user_id=slack_user_id,
        slack_email=slack_email,
        match_source=match_source,
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


def _extract_slack_emails(slack_user: dict[str, Any] | None) -> list[str]:
    if not slack_user:
        return []
    try:
        payload = json.dumps(slack_user)
    except TypeError:
        payload = str(slack_user)
    emails = {
        match.strip().lower()
        for match in EMAIL_PATTERN.findall(payload)
        if match and isinstance(match, str)
    }
    return sorted(email for email in emails if email)


async def upsert_slack_user_mapping_from_nango_action(
    organization_id: str,
    user_id: UUID,
    slack_user_payload: dict[str, Any] | None,
    match_source: str = "nango_slack_action",
) -> int:
    """Upsert Slack user mapping from a Nango action response."""
    if not slack_user_payload:
        logger.info(
            "[slack_conversations] Nango Slack user payload missing for org=%s user=%s",
            organization_id,
            user_id,
        )
        return 0

    slack_user_id = (
        slack_user_payload.get("id")
        or slack_user_payload.get("user_id")
        or slack_user_payload.get("user", {}).get("id")
    )
    if not slack_user_id:
        logger.warning(
            "[slack_conversations] Nango Slack user payload missing id for org=%s user=%s payload_keys=%s",
            organization_id,
            user_id,
            sorted(slack_user_payload.keys()),
        )
        return 0

    emails = _extract_slack_emails(slack_user_payload)
    slack_email = ",".join(emails) if emails else None
    logger.info(
        "[slack_conversations] Upserting Slack mapping from Nango action org=%s user=%s slack_user=%s emails=%s",
        organization_id,
        user_id,
        slack_user_id,
        emails,
    )
    await _upsert_slack_user_mapping(
        organization_id=organization_id,
        user_id=user_id,
        slack_user_id=slack_user_id,
        slack_email=slack_email,
        match_source=match_source,
    )
    return 1


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


def _extract_slack_timezone(slack_user: dict[str, Any] | None) -> str | None:
    """Extract the IANA timezone string (e.g. ``America/Los_Angeles``) from a
    Slack ``users.info`` response.  Returns ``None`` when unavailable."""
    if not slack_user:
        return None
    tz: str = (slack_user.get("tz") or "").strip()
    return tz or None


def _compute_local_time_iso(timezone: str | None) -> str | None:
    """Return the current time in *timezone* as an ISO-8601 string, or
    ``None`` if the timezone is unknown / invalid."""
    if not timezone:
        return None
    try:
        zone: ZoneInfo = ZoneInfo(timezone)
        return datetime.now(UTC).astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S")
    except (KeyError, Exception):
        logger.warning(
            "[slack_conversations] Invalid IANA timezone from Slack: %s", timezone,
        )
        return None


async def resolve_revtops_user_for_slack_actor(
    organization_id: str,
    slack_user_id: str,
    slack_user: dict[str, Any] | None = None,
) -> User | None:
    """Resolve the RevTops user linked to a Slack actor in this organization."""

    async with get_admin_session() as session:
        # Find users who belong to this org — either via their active org
        # or via the org_members table (multi-org support).
        org_uuid: UUID = UUID(organization_id)
        membership_subq = (
            select(OrgMember.user_id)
            .where(OrgMember.organization_id == org_uuid)
            .where(OrgMember.status == "active")
        )
        users_query = select(User).where(
            or_(
                User.organization_id == org_uuid,
                User.id.in_(membership_subq),
            )
        )
        users_result = await session.execute(users_query)
        org_users: list[User] = list(users_result.scalars().all())

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
            .where(SlackUserMapping.source == "slack")
            .where(SlackUserMapping.external_userid == slack_user_id)
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


def _merge_participating_user_ids(
    existing_ids: list[UUID] | None,
    candidate_user_id: str | None,
) -> list[UUID]:
    """Return participant UUIDs with candidate moved to the end as most recent."""
    merged_ids: list[UUID] = list(existing_ids or [])
    if not candidate_user_id:
        return merged_ids

    candidate_uuid: UUID = UUID(candidate_user_id)
    merged_ids = [participant for participant in merged_ids if participant != candidate_uuid]
    merged_ids.append(candidate_uuid)
    return merged_ids


def _resolve_current_revtops_user_id(
    linked_user: User | None,
    conversation: Conversation,
) -> str | None:
    """Pick the current user context using most recent speaker first, then historical fallback."""
    if linked_user:
        return str(linked_user.id)

    participant_ids: list[UUID] = list(conversation.participating_user_ids or [])
    if participant_ids:
        return str(participant_ids[-1])

    if conversation.user_id:
        return str(conversation.user_id)

    return None


def _resolve_thread_active_user_id(
    linked_user: User | None,
    conversation: Conversation,
    speaker_changed: bool,
) -> str | None:
    """Resolve thread active user, forcing handoff to the newest speaker."""
    if speaker_changed:
        return str(linked_user.id) if linked_user else None

    return _resolve_current_revtops_user_id(
        linked_user=linked_user,
        conversation=conversation,
    )


async def find_or_create_conversation(
    organization_id: str,
    slack_channel_id: str,
    slack_user_id: str,
    revtops_user_id: str | None,
    slack_user_name: str | None = None,
    slack_source: str = "dm",
    clear_current_user_on_unresolved: bool = False,
) -> Conversation:
    """
    Find an existing Slack conversation or create a new one.
    
    Conversations are keyed by (source='slack', source_channel_id).
    
    Args:
        organization_id: The organization this conversation belongs to
        slack_channel_id: Slack channel ID (plain for DMs, channel:thread_ts for mentions/threads)
        slack_user_id: Slack user ID who initiated the conversation
        revtops_user_id: Linked RevTops user UUID string if available
        slack_source: Origin type — "dm", "mention", or "thread"
        
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
            changed: bool = False
            previous_source_user_id: str | None = conversation.source_user_id

            if conversation.source_user_id != slack_user_id:
                conversation.source_user_id = slack_user_id
                changed = True
                logger.info(
                    "[slack_conversations] Updated conversation %s source_user_id from %s to %s",
                    conversation.id,
                    previous_source_user_id,
                    slack_user_id,
                )

            merged_participants = _merge_participating_user_ids(
                conversation.participating_user_ids,
                revtops_user_id,
            )
            if merged_participants != (conversation.participating_user_ids or []):
                conversation.participating_user_ids = merged_participants
                changed = True
                logger.info(
                    "[slack_conversations] Added participant to conversation %s participants=%s",
                    conversation.id,
                    [str(participant) for participant in merged_participants],
                )

            if revtops_user_id:
                resolved_user_id = UUID(revtops_user_id)
                if conversation.user_id != resolved_user_id:
                    conversation.user_id = resolved_user_id
                    changed = True
                    logger.info(
                        "[slack_conversations] Set conversation %s current user to %s",
                        conversation.id,
                        revtops_user_id,
                    )
            elif clear_current_user_on_unresolved and conversation.user_id is not None:
                previous_user_id: str = str(conversation.user_id)
                conversation.user_id = None
                changed = True
                logger.info(
                    "[slack_conversations] Cleared conversation %s current user (was %s) after unresolved Slack speaker %s",
                    conversation.id,
                    previous_user_id,
                    slack_user_id,
                )

            source_label: str = {"dm": "Slack DM", "mention": "Slack @mention", "thread": "Slack Thread"}.get(slack_source, "Slack")
            default_titles: set[str] = {"Slack DM", "Slack @mention", "Slack Thread", "Slack"}
            if slack_user_name and (not conversation.title or conversation.title in default_titles):
                conversation.title = f"{source_label} - {slack_user_name}"
                changed = True
                logger.info(
                    "[slack_conversations] Updated Slack conversation %s title to %s",
                    conversation.id,
                    conversation.title,
                )

            if changed:
                await session.commit()

            logger.info(
                "[slack_conversations] Found existing conversation %s for channel %s",
                conversation.id,
                slack_channel_id
            )
            return conversation
        
        # Create new conversation for this Slack channel/thread
        source_label = {"dm": "Slack DM", "mention": "Slack @mention", "thread": "Slack Thread"}.get(slack_source, "Slack")
        conversation = Conversation(
            organization_id=UUID(organization_id),
            user_id=UUID(revtops_user_id) if revtops_user_id else None,
            source="slack",
            source_channel_id=slack_channel_id,
            source_user_id=slack_user_id,
            participating_user_ids=_merge_participating_user_ids([], revtops_user_id),
            type="agent",
            title=f"{source_label} - {slack_user_name}" if slack_user_name else source_label,
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
    message_text: str,
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
        message_text: Message text
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
    slack_email: str | None = _extract_slack_email(slack_user)

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
                description=message_text[:1000],
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


async def _download_and_store_slack_files(
    connector: SlackConnector,
    files: list[dict[str, Any]],
) -> list[str]:
    """
    Download Slack file attachments and store them in the temp file store.

    Each file in *files* is a Slack file object (from the ``files`` array in
    a Slack event payload).  The bot token is used to authenticate the download.

    Returns:
        List of ``upload_id`` strings suitable for passing as
        ``attachment_ids`` to :meth:`ChatOrchestrator.process_message`.
    """
    from services.file_handler import store_file, MAX_FILE_SIZE

    attachment_ids: list[str] = []

    for slack_file in files:
        file_name: str = slack_file.get("name", "untitled")
        file_size: int = slack_file.get("size", 0)
        file_mimetype: str = slack_file.get("mimetype", "application/octet-stream")
        download_url: str = (
            slack_file.get("url_private_download")
            or slack_file.get("url_private", "")
        )

        if not download_url:
            logger.warning(
                "[slack_conversations] Slack file %s has no download URL — skipping",
                slack_file.get("id", "?"),
            )
            continue

        if file_size > MAX_FILE_SIZE:
            logger.warning(
                "[slack_conversations] Slack file %s (%s, %d bytes) exceeds max size — skipping",
                file_name,
                slack_file.get("id", "?"),
                file_size,
            )
            continue

        try:
            data: bytes = await connector.download_file(download_url)
            stored = store_file(
                filename=file_name,
                data=data,
                content_type=file_mimetype,
            )
            attachment_ids.append(stored.upload_id)
            logger.info(
                "[slack_conversations] Downloaded Slack file %s (%s, %d bytes) → %s",
                file_name,
                file_mimetype,
                len(data),
                stored.upload_id,
            )
        except Exception as e:
            logger.error(
                "[slack_conversations] Failed to download Slack file %s (%s): %s",
                file_name,
                slack_file.get("id", "?"),
                e,
            )

    return attachment_ids


async def _stream_and_post_responses(
    orchestrator: ChatOrchestrator,
    connector: SlackConnector,
    message_text: str,
    channel: str,
    thread_ts: str | None = None,
    attachment_ids: list[str] | None = None,
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
        attachment_ids: Optional upload IDs for attached files

    Returns:
        Total character count of all posted text.
    """
    current_text: str = ""
    total_length: int = 0

    try:
        async for chunk in orchestrator.process_message(
            message_text, attachment_ids=attachment_ids,
        ):
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
    files: list[dict[str, Any]] | None = None,
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
        files: Optional list of Slack file objects attached to the message
        
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
    slack_user_name: str | None = _extract_slack_display_name(slack_user)
    slack_user_email: str | None = _extract_slack_email(slack_user)
    slack_user_tz: str | None = _extract_slack_timezone(slack_user)
    if not linked_user:
        logger.info(
            "[slack_conversations] No linked RevTops user for Slack actor=%s org=%s; proceeding without user context",
            user_id,
            organization_id,
        )

    # Find or create conversation
    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=channel_id,
        slack_user_id=user_id,
        revtops_user_id=str(linked_user.id) if linked_user else None,
        slack_user_name=slack_user_name,
    )

    # Download any attached Slack files
    attachment_ids: list[str] = []
    if files:
        attachment_ids = await _download_and_store_slack_files(connector, files)

    # Process message through orchestrator
    local_time_iso: str | None = _compute_local_time_iso(slack_user_tz)
    orchestrator = ChatOrchestrator(
        user_id=str(linked_user.id) if linked_user else None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=linked_user.email if linked_user else None,
        source_user_id=user_id,
        source_user_email=slack_user_email,
        workflow_context=None,
        source="slack_dm",
        timezone=slack_user_tz,
        local_time=local_time_iso,
    )

    total_length: int = await _stream_and_post_responses(
        orchestrator=orchestrator,
        connector=connector,
        message_text=message_text or "(see attached files)",
        channel=channel_id,
        attachment_ids=attachment_ids or None,
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
    files: list[dict[str, Any]] | None = None,
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
        files: Optional list of Slack file objects attached to the message
        
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
    slack_user_name: str | None = _extract_slack_display_name(slack_user)
    slack_user_email: str | None = _extract_slack_email(slack_user)
    slack_user_tz: str | None = _extract_slack_timezone(slack_user)
    if not linked_user:
        logger.info(
            "[slack_conversations] No linked RevTops user for Slack actor=%s org=%s; proceeding without user context",
            user_id,
            organization_id,
        )
    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=source_channel_id,
        slack_user_id=user_id,
        revtops_user_id=str(linked_user.id) if linked_user else None,
        slack_user_name=slack_user_name,
        slack_source="mention",
    )

    # Download any attached Slack files
    attachment_ids: list[str] = []
    if files:
        attachment_ids = await _download_and_store_slack_files(connector, files)

    # Process message through orchestrator
    local_time_iso: str | None = _compute_local_time_iso(slack_user_tz)
    orchestrator = ChatOrchestrator(
        user_id=str(linked_user.id) if linked_user else None,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=linked_user.email if linked_user else None,
        source_user_id=user_id,
        source_user_email=slack_user_email,
        workflow_context={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
        source="slack_mention",
        timezone=slack_user_tz,
        local_time=local_time_iso,
    )

    total_length: int = await _stream_and_post_responses(
        orchestrator=orchestrator,
        connector=connector,
        message_text=message_text or "(see attached files)",
        channel=channel_id,
        thread_ts=thread_ts,
        attachment_ids=attachment_ids or None,
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
    files: list[dict[str, Any]] | None = None,
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
        files: Optional list of Slack file objects attached to the message

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

    speaker_changed: bool = conversation.source_user_id != user_id
    previous_source_user_id: str | None = conversation.source_user_id
    current_source_user_id: str = user_id if speaker_changed else (conversation.source_user_id or user_id)

    connector = SlackConnector(organization_id=organization_id)

    # Show a reaction on the user's reply immediately so they know the bot is working
    await connector.add_reaction(channel=channel_id, timestamp=event_ts)

    if speaker_changed:
        logger.info(
            "[slack_conversations] Thread %s:%s speaker handoff detected from %s to %s; applying source speaker handoff before additional processing",
            channel_id,
            thread_ts,
            previous_source_user_id,
            user_id,
        )
        conversation = await find_or_create_conversation(
            organization_id=organization_id,
            slack_channel_id=f"{channel_id}:{thread_ts}",
            slack_user_id=current_source_user_id,
            revtops_user_id=None,
            slack_source="thread",
            clear_current_user_on_unresolved=True,
        )

    slack_user = await _fetch_slack_user_info(
        organization_id=organization_id,
        slack_user_id=user_id,
    )
    slack_user_email: str | None = _extract_slack_email(slack_user)
    slack_user_tz: str | None = _extract_slack_timezone(slack_user)
    linked_user = await resolve_revtops_user_for_slack_actor(
        organization_id=organization_id,
        slack_user_id=user_id,
        slack_user=slack_user,
    )

    current_user_id: str | None = _resolve_thread_active_user_id(
        linked_user=linked_user,
        conversation=conversation,
        speaker_changed=speaker_changed,
    )
    current_user_email: str | None = linked_user.email if linked_user else None

    if speaker_changed:
        logger.info(
            "[slack_conversations] Thread %s:%s global context handoff to active user=%s completed for speaker=%s",
            channel_id,
            thread_ts,
            current_user_id,
            user_id,
        )

    # Ensure speaker and active user context are persisted before any further processing.
    conversation = await find_or_create_conversation(
        organization_id=organization_id,
        slack_channel_id=f"{channel_id}:{thread_ts}",
        slack_user_id=current_source_user_id,
        revtops_user_id=current_user_id,
        slack_source="thread",
        clear_current_user_on_unresolved=speaker_changed,
    )

    # Download any attached Slack files
    attachment_ids: list[str] = []
    if files:
        attachment_ids = await _download_and_store_slack_files(connector, files)

    # Process message through orchestrator, posting incrementally
    local_time_iso: str | None = _compute_local_time_iso(slack_user_tz)
    orchestrator = ChatOrchestrator(
        user_id=current_user_id,
        organization_id=organization_id,
        conversation_id=str(conversation.id),
        user_email=current_user_email,
        source_user_id=current_source_user_id,
        source_user_email=slack_user_email,
        workflow_context={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
        source="slack_thread",
        timezone=slack_user_tz,
        local_time=local_time_iso,
    )

    total_length: int = await _stream_and_post_responses(
        orchestrator=orchestrator,
        connector=connector,
        message_text=message_text or "(see attached files)",
        channel=channel_id,
        thread_ts=thread_ts,
        attachment_ids=attachment_ids or None,
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
