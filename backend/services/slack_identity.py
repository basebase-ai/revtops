"""
Slack identity mapping service.

Manages the link between Slack workspace users and RevTops internal users
via the ``user_mappings_for_identity`` table (``ExternalIdentityMapping``).

Previously this logic lived inside the monolithic ``slack_conversations.py``;
it was extracted here so the conversation-processing code could be replaced
by the messenger framework while identity management remains available to
connectors, auth routes, and the Slack connector sync flow.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_nango_integration_id, settings
from connectors.slack import SlackConnector
from models.database import get_admin_session, get_session
from models.external_identity_mapping import ExternalIdentityMapping
from models.integration import Integration
from models.org_member import OrgMember
from models.organization import Organization
from models.user import User
from services.nango import extract_connection_metadata, get_nango_client

logger = logging.getLogger(__name__)

EMAIL_PATTERN: re.Pattern[str] = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_slack_user_id(slack_user_id: str | None) -> str:
    return (slack_user_id or "").strip().upper()


def _normalize_slack_team_id(team_id: str | None) -> str:
    return (team_id or "").strip().upper()


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _slack_mapping_source_clause() -> Any:
    """Return source filter for Slack identity mappings, including legacy rows."""
    return or_(
        ExternalIdentityMapping.source == "slack",
        ExternalIdentityMapping.source == "revtops_unknown",
    )


# ---------------------------------------------------------------------------
# Slack user-info cache (process-local)
# ---------------------------------------------------------------------------

_SLACK_USER_INFO_CACHE_TTL_SUCCESS_SECONDS: int = 600
_SLACK_USER_INFO_CACHE_TTL_NOT_FOUND_SECONDS: int = 120
_SLACK_USER_INFO_CACHE_MAX_ENTRIES: int = 5000
_slack_user_info_cache: dict[tuple[str, str], tuple[dict[str, Any] | None, float]] = {}
_slack_user_info_cache_lock: asyncio.Lock = asyncio.Lock()


def _slack_user_info_cache_evict_expired(now: float) -> None:
    expired = [k for k, (_, exp) in _slack_user_info_cache.items() if exp <= now]
    for k in expired:
        del _slack_user_info_cache[k]


async def _fetch_slack_user_info(
    organization_id: str,
    slack_user_id: str,
) -> dict[str, Any] | None:
    key: tuple[str, str] = (organization_id, slack_user_id)
    now: float = time.monotonic()
    async with _slack_user_info_cache_lock:
        entry = _slack_user_info_cache.get(key)
        if entry is not None:
            cached_val, expires_at = entry
            if now < expires_at:
                return cached_val
            del _slack_user_info_cache[key]

    try:
        logger.info(
            "[slack_identity] Fetching Slack user info for user=%s org=%s",
            slack_user_id, organization_id,
        )
        connector = SlackConnector(organization_id=organization_id)
        result: dict[str, Any] = await connector.get_user_info(slack_user_id)
        ttl: int = _SLACK_USER_INFO_CACHE_TTL_SUCCESS_SECONDS
        async with _slack_user_info_cache_lock:
            if len(_slack_user_info_cache) >= _SLACK_USER_INFO_CACHE_MAX_ENTRIES:
                _slack_user_info_cache_evict_expired(now)
            if len(_slack_user_info_cache) < _SLACK_USER_INFO_CACHE_MAX_ENTRIES:
                _slack_user_info_cache[key] = (result, now + ttl)
        return result
    except Exception as exc:
        logger.warning(
            "[slack_identity] Failed Slack users.info lookup for user=%s org=%s: %s",
            slack_user_id, organization_id, exc, exc_info=True,
        )
        ttl = _SLACK_USER_INFO_CACHE_TTL_NOT_FOUND_SECONDS
        async with _slack_user_info_cache_lock:
            if len(_slack_user_info_cache) >= _SLACK_USER_INFO_CACHE_MAX_ENTRIES:
                _slack_user_info_cache_evict_expired(now)
            if len(_slack_user_info_cache) < _SLACK_USER_INFO_CACHE_MAX_ENTRIES:
                _slack_user_info_cache[key] = (None, now + ttl)
        return None


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

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


def _extract_slack_email(slack_user: dict[str, Any] | None) -> str | None:
    if not slack_user:
        return None
    profile: dict[str, Any] = slack_user.get("profile", {})
    slack_email: str = (profile.get("email") or "").strip().lower()
    return slack_email or None


def _extract_slack_emails(slack_user: dict[str, Any] | None) -> list[str]:
    if not slack_user:
        return []
    try:
        payload: str = json.dumps(slack_user)
    except TypeError:
        payload = str(slack_user)
    emails: set[str] = {
        match.strip().lower()
        for match in EMAIL_PATTERN.findall(payload)
        if match and isinstance(match, str)
    }
    return sorted(email for email in emails if email)


def _extract_slack_display_name(slack_user: dict[str, Any] | None) -> str | None:
    if not slack_user:
        return None
    profile: dict[str, Any] = slack_user.get("profile", {})
    display_name: str = (
        profile.get("display_name")
        or profile.get("real_name")
        or slack_user.get("real_name")
        or slack_user.get("name")
        or ""
    ).strip()
    return display_name or None


def _extract_slack_timezone(slack_user: dict[str, Any] | None) -> str | None:
    if not slack_user:
        return None
    tz: str = (slack_user.get("tz") or "").strip()
    return tz or None


def _compute_local_time_iso(timezone: str | None) -> str | None:
    if not timezone:
        return None
    try:
        zone: ZoneInfo = ZoneInfo(timezone)
        return datetime.now(UTC).astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S")
    except (KeyError, Exception):
        logger.warning("[slack_identity] Invalid IANA timezone from Slack: %s", timezone)
        return None


# ---------------------------------------------------------------------------
# Guest user resolution
# ---------------------------------------------------------------------------

async def _resolve_guest_user_for_org(organization_id: str) -> User | None:
    async with get_admin_session() as session:
        org = await session.get(Organization, UUID(organization_id))
        if not org or not org.guest_user_enabled or not org.guest_user_id:
            return None
        guest_user = await session.get(User, org.guest_user_id)
        if not guest_user or not guest_user.is_guest:
            logger.warning(
                "[slack_identity] Guest user misconfigured org=%s guest_user_id=%s",
                organization_id, org.guest_user_id,
            )
            return None
        logger.info(
            "[slack_identity] Using enabled guest user org=%s guest_user=%s",
            organization_id, guest_user.id,
        )
        return guest_user


async def _resolve_guest_user_after_unmapped_actor(
    organization_id: str,
    normalized_slack_user_id: str,
    reason: str,
) -> User | None:
    logger.info(
        "[slack_identity] Attempting guest fallback for Slack actor user=%s org=%s reason=%s",
        normalized_slack_user_id, organization_id, reason,
    )
    return await _resolve_guest_user_for_org(organization_id)


# ---------------------------------------------------------------------------
# Integration metadata helpers
# ---------------------------------------------------------------------------

async def _update_integration_metadata(
    integration_id: UUID,
    metadata: dict[str, Any],
) -> None:
    async with get_admin_session() as session:
        stmt = (
            update(Integration)
            .where(Integration.id == integration_id)
            .values(extra_data=metadata, updated_at=datetime.utcnow())
        )
        await session.execute(stmt)
        await session.commit()


async def _update_integration_connected_user(
    integration_id: UUID,
    user_id: UUID,
) -> None:
    try:
        async with get_admin_session() as session:
            stmt = (
                update(Integration)
                .where(Integration.id == integration_id)
                .values(connected_by_user_id=user_id, updated_at=datetime.utcnow())
            )
            await session.execute(stmt)
            await session.commit()
            logger.info(
                "[slack_identity] Updated Slack integration %s connected_by_user_id=%s",
                integration_id, user_id,
            )
    except Exception as exc:
        logger.warning(
            "[slack_identity] Failed to update Slack integration %s connected_by_user_id=%s: %s",
            integration_id, user_id, exc, exc_info=True,
        )


async def _hydrate_slack_integration_metadata(integration: Integration) -> None:
    extra_data: dict[str, Any] = integration.extra_data or {}
    slack_user_ids: set[str] = _extract_slack_user_ids(extra_data)
    if slack_user_ids:
        return

    if not integration.nango_connection_id:
        logger.info(
            "[slack_identity] Slack integration %s missing metadata and nango_connection_id",
            integration.id,
        )
        return

    try:
        nango = get_nango_client()
        integration_id = get_nango_integration_id("slack")
        connection = await nango.get_connection(integration_id, integration.nango_connection_id)
        connection_metadata: dict[str, Any] = extract_connection_metadata(connection) or {}
        if not connection_metadata:
            logger.info(
                "[slack_identity] Slack integration %s has no metadata in Nango connection keys=%s",
                integration.id, sorted(connection.keys()),
            )
            return

        slack_user_ids = _extract_slack_user_ids(connection_metadata)
        if not slack_user_ids:
            logger.info(
                "[slack_identity] Slack integration %s metadata missing Slack user IDs keys=%s",
                integration.id, sorted(connection_metadata.keys()),
            )
        await _update_integration_metadata(
            integration_id=integration.id, metadata=connection_metadata,
        )
        integration.extra_data = connection_metadata
        logger.info(
            "[slack_identity] Refreshed Slack integration %s metadata from Nango keys=%s",
            integration.id, sorted(connection_metadata.keys()),
        )
    except Exception as exc:
        logger.warning(
            "[slack_identity] Failed to hydrate Slack integration %s metadata from Nango: %s",
            integration.id, exc, exc_info=True,
        )


async def _resolve_user_for_slack_integration(
    organization_id: str,
    integration: Integration,
) -> User | None:
    extra_data: dict[str, Any] = integration.extra_data or {}
    slack_user_ids: list[str] = sorted(_extract_slack_user_ids(extra_data))
    if not slack_user_ids:
        logger.info(
            "[slack_identity] Slack integration %s has no Slack user IDs in metadata",
            integration.id,
        )
        return None

    logger.info(
        "[slack_identity] Attempting to resolve Slack integration %s user via Slack IDs=%s",
        integration.id, slack_user_ids,
    )
    for slack_user_id in slack_user_ids:
        resolved_user: User | None = await resolve_revtops_user_for_slack_actor(
            organization_id=organization_id,
            slack_user_id=slack_user_id,
        )
        if resolved_user:
            if not integration.connected_by_user_id:
                await _update_integration_connected_user(
                    integration_id=integration.id, user_id=resolved_user.id,
                )
            return resolved_user

    logger.info(
        "[slack_identity] No RevTops user resolved for Slack integration %s via Slack IDs=%s",
        integration.id, slack_user_ids,
    )
    return None


# ---------------------------------------------------------------------------
# Core upsert
# ---------------------------------------------------------------------------

async def _upsert_slack_user_mapping(
    organization_id: str,
    user_id: UUID | None,
    slack_user_id: str | None,
    slack_email: str | None,
    match_source: str,
    revtops_email: str | None = None,
) -> None:
    now: datetime = datetime.now(UTC).replace(tzinfo=None)
    normalized_slack_user_id: str = _normalize_slack_user_id(slack_user_id)
    if not normalized_slack_user_id:
        logger.warning(
            "[slack_identity] Skipping mapping upsert org=%s user=%s — missing slack_user_id",
            organization_id, user_id,
        )
        return
    try:
        logger.info(
            "[slack_identity] Attempting mapping upsert org=%s user=%s slack_user=%s email=%s source=%s",
            organization_id, user_id, normalized_slack_user_id, slack_email, match_source,
        )
        async with get_admin_session() as session:
            resolved_revtops_email: str | None = revtops_email
            if user_id and not resolved_revtops_email:
                result = await session.execute(
                    select(User.email).where(User.id == user_id)
                )
                user_email = result.scalar_one_or_none()
                if isinstance(user_email, str) and user_email.strip():
                    resolved_revtops_email = user_email.strip().lower()

            if user_id:
                existing_result = await session.execute(
                    select(ExternalIdentityMapping)
                    .where(ExternalIdentityMapping.organization_id == UUID(organization_id))
                    .where(_slack_mapping_source_clause())
                    .where(ExternalIdentityMapping.external_userid == normalized_slack_user_id)
                    .order_by(ExternalIdentityMapping.updated_at.desc())
                    .limit(1)
                )
                existing_mapping: ExternalIdentityMapping | None = existing_result.scalar_one_or_none()
                if existing_mapping:
                    if existing_mapping.match_source == "manual_unlink":
                        logger.info(
                            "[slack_identity] Skipping — manually unlinked org=%s slack_user=%s user=%s",
                            organization_id, normalized_slack_user_id, user_id,
                        )
                        return
                    if existing_mapping.user_id is None:
                        existing_mapping.user_id = user_id
                        existing_mapping.revtops_email = resolved_revtops_email
                        existing_mapping.external_email = slack_email
                        existing_mapping.source = "slack"
                        existing_mapping.match_source = match_source
                        existing_mapping.updated_at = now
                        await session.commit()
                        logger.info(
                            "[slack_identity] Promoted unmapped row org=%s slack_user=%s → user=%s",
                            organization_id, normalized_slack_user_id, user_id,
                        )
                        return

                    if existing_mapping.user_id != user_id:
                        logger.info(
                            "[slack_identity] Skipping — Slack user already mapped to different user org=%s slack_user=%s existing=%s requested=%s",
                            organization_id, normalized_slack_user_id, existing_mapping.user_id, user_id,
                        )
                        return

                    existing_mapping.revtops_email = resolved_revtops_email
                    existing_mapping.external_email = slack_email
                    existing_mapping.source = "slack"
                    existing_mapping.match_source = match_source
                    existing_mapping.updated_at = now
                    await session.commit()
                    logger.info(
                        "[slack_identity] Refreshed existing mapping org=%s user=%s slack_user=%s",
                        organization_id, user_id, normalized_slack_user_id,
                    )
                    return

                stmt = pg_insert(ExternalIdentityMapping).values(
                    id=uuid.uuid4(),
                    organization_id=UUID(organization_id),
                    user_id=user_id,
                    revtops_email=resolved_revtops_email,
                    external_userid=normalized_slack_user_id,
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
                    "[slack_identity] Upserted mapping org=%s user=%s slack_user=%s",
                    organization_id, user_id, normalized_slack_user_id,
                )
                return

            any_existing_result = await session.execute(
                select(ExternalIdentityMapping)
                .where(ExternalIdentityMapping.organization_id == UUID(organization_id))
                .where(_slack_mapping_source_clause())
                .where(ExternalIdentityMapping.external_userid == normalized_slack_user_id)
                .limit(1)
            )
            any_existing: ExternalIdentityMapping | None = any_existing_result.scalar_one_or_none()
            if any_existing:
                if any_existing.user_id is None:
                    if any_existing.match_source == "manual_unlink":
                        logger.info(
                            "[slack_identity] Preserving manually unlinked mapping org=%s slack_user=%s",
                            organization_id, normalized_slack_user_id,
                        )
                        return
                    any_existing.external_email = slack_email
                    any_existing.source = "slack"
                    any_existing.match_source = match_source
                    any_existing.updated_at = now
                    await session.commit()
                    logger.info(
                        "[slack_identity] Updated unmapped row org=%s slack_user=%s",
                        organization_id, normalized_slack_user_id,
                    )
                else:
                    logger.info(
                        "[slack_identity] Skipping unmapped upsert — already mapped org=%s slack_user=%s user=%s",
                        organization_id, normalized_slack_user_id, any_existing.user_id,
                    )
                return

            mapping = ExternalIdentityMapping(
                id=uuid.uuid4(),
                organization_id=UUID(organization_id),
                user_id=None,
                revtops_email=resolved_revtops_email,
                external_userid=normalized_slack_user_id,
                external_email=slack_email,
                source="slack",
                match_source=match_source,
                created_at=now,
                updated_at=now,
            )
            session.add(mapping)
            await session.commit()
            logger.info(
                "[slack_identity] Created unmapped row org=%s slack_user=%s",
                organization_id, normalized_slack_user_id,
            )
    except Exception as exc:
        logger.warning(
            "[slack_identity] Failed to upsert mapping org=%s user=%s slack_user=%s: %s",
            organization_id, user_id, slack_user_id, exc, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Public API — used by routes, connectors, etc.
# ---------------------------------------------------------------------------

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


async def get_slack_user_ids_for_revtops_user(
    organization_id: str,
    user_id: str,
    session: AsyncSession | None = None,
) -> set[str]:
    """Return Slack user IDs associated with a RevTops user in this org."""
    user_uuid: UUID = UUID(user_id)

    async def _query(sess: AsyncSession) -> tuple[list[ExternalIdentityMapping], list[Integration]]:
        mappings_result = await sess.execute(
            select(ExternalIdentityMapping)
            .where(ExternalIdentityMapping.organization_id == UUID(organization_id))
            .where(_slack_mapping_source_clause())
            .where(ExternalIdentityMapping.user_id == user_uuid)
        )
        integrations_result = await sess.execute(
            select(Integration)
            .where(Integration.organization_id == UUID(organization_id))
            .where(Integration.connector == "slack")
            .where(Integration.is_active == True)  # noqa: E712
        )
        return list(mappings_result.scalars().all()), list(integrations_result.scalars().all())

    if session is not None:
        slack_mappings, slack_integrations = await _query(session)
    else:
        async with get_admin_session() as admin_sess:
            slack_mappings, slack_integrations = await _query(admin_sess)

    slack_user_ids: set[str] = set()
    for mapping in slack_mappings:
        if mapping.external_userid:
            slack_user_ids.add(mapping.external_userid)

    logger.info(
        "[slack_identity] Resolved %d Slack user IDs for org=%s user=%s (mappings=%d)",
        len(slack_user_ids), organization_id, user_id, len(slack_mappings),
    )
    if not slack_user_ids and slack_integrations:
        logger.info(
            "[slack_identity] No Slack user mappings for org=%s user=%s with %d active integrations",
            organization_id, user_id, len(slack_integrations),
        )
    return slack_user_ids


async def upsert_slack_user_mappings_from_metadata(
    organization_id: str,
    user_id: UUID,
    integration_metadata: dict[str, Any] | None,
) -> int:
    """Upsert Slack user mappings based on integration metadata for a user."""
    slack_user_ids: set[str] = _extract_slack_user_ids(integration_metadata or {})
    if not slack_user_ids:
        logger.info(
            "[slack_identity] No Slack user IDs in integration metadata for org=%s user=%s",
            organization_id, user_id,
        )
        return 0

    created_count: int = 0
    for slack_user_id in sorted(slack_user_ids):
        slack_user: dict[str, Any] | None = await _fetch_slack_user_info(
            organization_id=organization_id,
            slack_user_id=slack_user_id,
        )
        slack_email: str | None = _extract_slack_email(slack_user)
        await _upsert_slack_user_mapping(
            organization_id=organization_id,
            user_id=user_id,
            slack_user_id=slack_user_id,
            slack_email=slack_email,
            match_source="slack_integration_metadata",
        )
        created_count += 1

    logger.info(
        "[slack_identity] Upserted %d mappings from integration metadata for org=%s user=%s",
        created_count, organization_id, user_id,
    )
    return created_count


async def refresh_slack_user_mappings_for_org(organization_id: str) -> int:
    """Refresh Slack user mappings for active Slack integrations in an org."""
    async with get_admin_session() as session:
        integrations_result = await session.execute(
            select(Integration)
            .where(Integration.organization_id == UUID(organization_id))
            .where(Integration.connector == "slack")
            .where(Integration.is_active == True)  # noqa: E712
        )
        slack_integrations: list[Integration] = list(integrations_result.scalars().all())

    if not slack_integrations:
        logger.info("[slack_identity] No active Slack integrations for org=%s", organization_id)
        return 0

    total_created: int = 0
    for integration in slack_integrations:
        await _hydrate_slack_integration_metadata(integration)
        target_user_id: UUID | None = integration.user_id or integration.connected_by_user_id
        if not target_user_id:
            resolved_user: User | None = await _resolve_user_for_slack_integration(
                organization_id=organization_id, integration=integration,
            )
            if resolved_user:
                target_user_id = resolved_user.id
            else:
                logger.warning(
                    "[slack_identity] Unable to resolve user for integration %s; skipping",
                    integration.id,
                )
                continue
        created_count: int = await upsert_slack_user_mappings_from_metadata(
            organization_id=organization_id,
            user_id=target_user_id,
            integration_metadata=integration.extra_data or {},
        )
        total_created += created_count

    logger.info(
        "[slack_identity] Refreshed %d mappings for org=%s", total_created, organization_id,
    )
    return total_created


async def refresh_slack_user_mappings_from_directory(
    organization_id: str,
    connector: SlackConnector,
) -> int:
    """Refresh Slack user mappings by matching Slack directory emails to RevTops users."""
    logger.info("[slack_identity] Starting directory mapping refresh for org=%s", organization_id)
    async with get_admin_session() as session:
        org_uuid: UUID = UUID(organization_id)
        membership_subq = (
            select(OrgMember.user_id)
            .where(OrgMember.organization_id == org_uuid)
            .where(OrgMember.status.in_(("active", "onboarding")))
        )
        users_result = await session.execute(
            select(User).where(
                or_(
                    User.organization_id == org_uuid,
                    User.id.in_(membership_subq),
                )
            )
        )
        org_users: list[User] = list(users_result.scalars().all())

    email_to_user: dict[str, User] = {}
    for user in org_users:
        if user.email:
            email_to_user[user.email.strip().lower()] = user

    logger.info(
        "[slack_identity] Loaded %d org users with %d emails for org=%s",
        len(org_users), len(email_to_user), organization_id,
    )

    slack_users: list[dict[str, Any]] = await connector.get_users()
    logger.info(
        "[slack_identity] Retrieved %d Slack users for org=%s", len(slack_users), organization_id,
    )

    mapped_count: int = 0
    for slack_user in slack_users:
        slack_user_id: str | None = slack_user.get("id")
        if not slack_user_id:
            continue
        if slack_user.get("deleted") or slack_user.get("is_bot"):
            continue

        slack_user_payload: dict[str, Any] | None = slack_user
        if not slack_user.get("profile"):
            slack_user_payload = await _fetch_slack_user_info(
                organization_id=organization_id, slack_user_id=slack_user_id,
            )
        slack_email: str | None = _extract_slack_email(slack_user_payload)

        matched_user: User | None = email_to_user.get(slack_email) if slack_email else None

        if matched_user:
            logger.info(
                "[slack_identity] Matched Slack user=%s email=%s → RevTops user=%s",
                slack_user_id, slack_email, matched_user.id,
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
            await _upsert_slack_user_mapping(
                organization_id=organization_id,
                user_id=None,
                slack_user_id=slack_user_id,
                slack_email=slack_email,
                match_source="slack_directory_unmapped",
            )

    logger.info(
        "[slack_identity] Directory mapping refresh complete org=%s mapped=%d",
        organization_id, mapped_count,
    )
    return mapped_count


async def upsert_slack_user_mapping_from_nango_action(
    organization_id: str,
    user_id: UUID,
    slack_user_payload: dict[str, Any] | None,
    match_source: str = "nango_slack_action",
) -> int:
    """Upsert Slack user mapping from a Nango action response."""
    if not slack_user_payload:
        logger.info(
            "[slack_identity] Nango Slack user payload missing for org=%s user=%s",
            organization_id, user_id,
        )
        return 0

    slack_user_id: str | None = (
        slack_user_payload.get("id")
        or slack_user_payload.get("user_id")
        or slack_user_payload.get("user", {}).get("id")
    )
    if not slack_user_id:
        logger.warning(
            "[slack_identity] Nango Slack user payload missing id for org=%s user=%s keys=%s",
            organization_id, user_id, sorted(slack_user_payload.keys()),
        )
        return 0

    emails: list[str] = _extract_slack_emails(slack_user_payload)
    slack_email: str | None = ",".join(emails) if emails else None
    logger.info(
        "[slack_identity] Upserting mapping from Nango action org=%s user=%s slack_user=%s",
        organization_id, user_id, slack_user_id,
    )
    await _upsert_slack_user_mapping(
        organization_id=organization_id,
        user_id=user_id,
        slack_user_id=slack_user_id,
        slack_email=slack_email,
        match_source=match_source,
    )
    return 1


async def resolve_revtops_user_for_slack_actor(
    organization_id: str,
    slack_user_id: str,
    slack_user: dict[str, Any] | None = None,
) -> User | None:
    """Resolve the RevTops user linked to a Slack actor in this organization."""
    normalized_slack_user_id: str = _normalize_slack_user_id(slack_user_id)
    if not normalized_slack_user_id:
        return await _resolve_guest_user_after_unmapped_actor(
            organization_id=organization_id,
            normalized_slack_user_id=normalized_slack_user_id,
            reason="empty_slack_user_id",
        )

    async with get_admin_session() as session:
        org_uuid: UUID = UUID(organization_id)
        membership_subq = (
            select(OrgMember.user_id)
            .where(OrgMember.organization_id == org_uuid)
            .where(OrgMember.status.in_(("active", "onboarding")))
        )
        users_result = await session.execute(
            select(User).where(
                or_(
                    User.organization_id == org_uuid,
                    User.id.in_(membership_subq),
                )
            )
        )
        org_users: list[User] = list(users_result.scalars().all())

        integrations_result = await session.execute(
            select(Integration)
            .where(Integration.organization_id == UUID(organization_id))
            .where(Integration.connector == "slack")
            .where(Integration.is_active == True)  # noqa: E712
        )
        slack_integrations: list[Integration] = list(integrations_result.scalars().all())

        mappings_result = await session.execute(
            select(ExternalIdentityMapping)
            .where(ExternalIdentityMapping.organization_id == UUID(organization_id))
            .where(_slack_mapping_source_clause())
            .where(ExternalIdentityMapping.external_userid == normalized_slack_user_id)
        )
        existing_mappings: list[ExternalIdentityMapping] = list(mappings_result.scalars().all())

    if existing_mappings:
        latest_mapping: ExternalIdentityMapping = max(
            existing_mappings,
            key=lambda m: getattr(m, "updated_at", datetime.min),
        )
        for user in org_users:
            if user.id == latest_mapping.user_id:
                logger.info(
                    "[slack_identity] Resolved Slack user=%s via stored mapping → user=%s",
                    normalized_slack_user_id, user.id,
                )
                return user
        logger.info(
            "[slack_identity] Stored mapping for Slack user=%s references missing user=%s",
            normalized_slack_user_id, latest_mapping.user_id,
        )

    for integration in slack_integrations:
        extra_data: dict[str, Any] = integration.extra_data or {}
        integration_slack_ids: set[str] = _extract_slack_user_ids(extra_data)
        if normalized_slack_user_id in integration_slack_ids:
            target_user_id: UUID | None = integration.user_id or integration.connected_by_user_id
            if not target_user_id:
                continue
            for user in org_users:
                if user.id == target_user_id:
                    logger.info(
                        "[slack_identity] Matched Slack user=%s via integration metadata → user=%s",
                        normalized_slack_user_id, user.id,
                    )
                    await _upsert_slack_user_mapping(
                        organization_id=organization_id,
                        user_id=user.id,
                        slack_user_id=normalized_slack_user_id,
                        slack_email=None,
                        match_source="slack_integration",
                    )
                    return user

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
        return await _resolve_guest_user_after_unmapped_actor(
            organization_id=organization_id,
            normalized_slack_user_id=normalized_slack_user_id,
            reason="no_org_users",
        )

    try:
        slack_user = slack_user or await _fetch_slack_user_info(
            organization_id=organization_id, slack_user_id=slack_user_id,
        )
        if not slack_user:
            return await _resolve_guest_user_after_unmapped_actor(
                organization_id=organization_id,
                normalized_slack_user_id=normalized_slack_user_id,
                reason="missing_slack_user_profile",
            )
        profile: dict[str, Any] = slack_user.get("profile", {})
        slack_email: str = (profile.get("email") or "").strip().lower()
        slack_names: set[str] = {
            _normalize_name(slack_user.get("name")),
            _normalize_name(slack_user.get("real_name")),
            _normalize_name(profile.get("display_name")),
            _normalize_name(profile.get("real_name")),
            _normalize_name(profile.get("display_name_normalized")),
            _normalize_name(profile.get("real_name_normalized")),
        }
        slack_names.discard("")

        logger.info(
            "[slack_identity] Slack user resolution lookup user=%s has_email=%s candidate_names=%s",
            normalized_slack_user_id, bool(slack_email), sorted(slack_names),
        )
    except Exception as exc:
        logger.warning(
            "[slack_identity] Failed Slack user resolution for user=%s org=%s: %s",
            normalized_slack_user_id, organization_id, exc, exc_info=True,
        )
        return await _resolve_guest_user_after_unmapped_actor(
            organization_id=organization_id,
            normalized_slack_user_id=normalized_slack_user_id,
            reason="profile_lookup_error",
        )

    if slack_email:
        for user in org_users:
            if user.email and user.email.strip().lower() == slack_email:
                logger.info(
                    "[slack_identity] Matched Slack user=%s email=%s → user=%s",
                    normalized_slack_user_id, slack_email, user.id,
                )
                await _upsert_slack_user_mapping(
                    organization_id=organization_id,
                    user_id=user.id,
                    slack_user_id=normalized_slack_user_id,
                    slack_email=slack_email,
                    match_source="email",
                )
                return user

    if slack_names and users_with_connected_slack:
        for user in org_users:
            if user.id not in users_with_connected_slack:
                continue
            user_name: str = _normalize_name(user.name)
            if user_name and user_name in slack_names:
                logger.info(
                    "[slack_identity] Matched Slack user=%s by name=%s → connected user=%s",
                    normalized_slack_user_id, user_name, user.id,
                )
                return user

    logger.info(
        "[slack_identity] Failed to resolve RevTops user for Slack actor=%s org=%s",
        normalized_slack_user_id, organization_id,
    )
    guest_user: User | None = await _resolve_guest_user_after_unmapped_actor(
        organization_id=organization_id,
        normalized_slack_user_id=normalized_slack_user_id,
        reason="no_mapping_match",
    )
    return guest_user


async def ingest_unknown_slack_actor_and_retry_mapping(
    organization_id: str,
    team_id: str,
    slack_user_id: str,
    slack_user: dict[str, Any] | None,
) -> User | None:
    """Ingest latest Slack identity data for an unresolved actor, then retry mapping."""
    normalized_slack_user_id: str = _normalize_slack_user_id(slack_user_id)
    logger.info(
        "[slack_identity] Ingesting unknown Slack actor before refusal org=%s team=%s user=%s",
        organization_id, team_id, normalized_slack_user_id,
    )
    try:
        resolved_slack_user: dict[str, Any] | None = slack_user or await _fetch_slack_user_info(
            organization_id=organization_id, slack_user_id=slack_user_id,
        )
        resolved_email: str | None = _extract_slack_email(resolved_slack_user)
        await _upsert_slack_user_mapping(
            organization_id=organization_id,
            user_id=None,
            slack_user_id=normalized_slack_user_id,
            slack_email=resolved_email,
            match_source="unknown_actor_ingest",
        )

        await refresh_slack_user_mappings_for_org(organization_id)
        connector = SlackConnector(organization_id=organization_id, team_id=team_id)
        await refresh_slack_user_mappings_from_directory(organization_id, connector)

        resolved_user: User | None = await resolve_revtops_user_for_slack_actor(
            organization_id=organization_id,
            slack_user_id=normalized_slack_user_id,
            slack_user=resolved_slack_user,
        )
        logger.info(
            "[slack_identity] Unknown actor ingestion result org=%s user=%s mapped=%s",
            organization_id, normalized_slack_user_id, bool(resolved_user),
        )
        return resolved_user
    except Exception:
        logger.exception(
            "[slack_identity] Failed ingestion pass for unknown Slack actor org=%s team=%s user=%s",
            organization_id, team_id, normalized_slack_user_id,
        )
        return None
