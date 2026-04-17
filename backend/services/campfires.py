from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models.content_group import (
    Campfire,
    CampfireContentGroup,
    ContentGroup,
    ContentGroupSummary,
)
from models.database import get_session

logger = logging.getLogger(__name__)


async def create_campfire(
    organization_id: str,
    name: str,
    description: str | None = None,
    created_by_user_id: str | None = None,
) -> Campfire:
    async with get_session(organization_id=organization_id, user_id=created_by_user_id) as session:
        campfire = Campfire(
            organization_id=UUID(organization_id),
            name=name,
            description=description,
            created_by_user_id=UUID(created_by_user_id) if created_by_user_id else None,
        )
        session.add(campfire)
        await session.commit()
        await session.refresh(campfire)
        return campfire


async def update_campfire(
    organization_id: str,
    campfire_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    is_archived: bool | None = None,
) -> Campfire | None:
    async with get_session(organization_id=organization_id) as session:
        campfire = await session.get(Campfire, UUID(campfire_id))
        if campfire is None or str(campfire.organization_id) != organization_id:
            return None
        if name is not None:
            campfire.name = name
        if description is not None:
            campfire.description = description
        if is_archived is not None:
            campfire.is_archived = is_archived
        campfire.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(campfire)
        return campfire


async def add_content_group_to_campfire(
    organization_id: str,
    campfire_id: str,
    content_group_id: str,
) -> None:
    org_uuid = UUID(organization_id)
    async with get_session(organization_id=organization_id) as session:
        campfire_stmt = (
            select(Campfire.id)
            .where(Campfire.id == UUID(campfire_id))
            .where(Campfire.organization_id == org_uuid)
        )
        content_group_stmt = (
            select(ContentGroup.id)
            .where(ContentGroup.id == UUID(content_group_id))
            .where(ContentGroup.organization_id == org_uuid)
        )
        campfire_exists = (await session.execute(campfire_stmt)).scalar_one_or_none()
        content_group_exists = (await session.execute(content_group_stmt)).scalar_one_or_none()
        if campfire_exists is None or content_group_exists is None:
            logger.warning(
                "[campfire.add_content_group] org_mismatch_or_not_found org_id=%s campfire_id=%s content_group_id=%s",
                organization_id,
                campfire_id,
                content_group_id,
            )
            return

        stmt = pg_insert(CampfireContentGroup).values(
            campfire_id=UUID(campfire_id),
            content_group_id=UUID(content_group_id),
        ).on_conflict_do_nothing(
            constraint="uq_campfire_content_group"
        )
        await session.execute(stmt)
        await session.commit()


async def remove_content_group_from_campfire(
    organization_id: str,
    campfire_id: str,
    content_group_id: str,
) -> int:
    async with get_session(organization_id=organization_id) as session:
        stmt = (
            CampfireContentGroup.__table__.delete()
            .where(CampfireContentGroup.campfire_id == UUID(campfire_id))
            .where(CampfireContentGroup.content_group_id == UUID(content_group_id))
        )
        result = await session.execute(stmt)
        await session.commit()
        return int(result.rowcount or 0)


async def list_campfires_by_content_group(
    organization_id: str,
    content_group_id: str,
) -> list[Campfire]:
    stmt = (
        select(Campfire)
        .join(CampfireContentGroup, CampfireContentGroup.campfire_id == Campfire.id)
        .where(Campfire.organization_id == UUID(organization_id))
        .where(CampfireContentGroup.content_group_id == UUID(content_group_id))
        .where(Campfire.is_archived == False)  # noqa: E712
        .order_by(Campfire.updated_at.desc())
    )
    async with get_session(organization_id=organization_id) as session:
        started = datetime.now(UTC)
        rows = await session.execute(stmt)
        campfires = list(rows.scalars().all())
        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        logger.info(
            "[campfire.lookup] org_id=%s content_group_id=%s campfire_count=%d campfire_lookup_ms=%d",
            organization_id,
            content_group_id,
            len(campfires),
            elapsed_ms,
        )
        return campfires


async def list_campfire_context_summaries(
    organization_id: str,
    content_group_id: str,
    *,
    summary_limit_per_group: int = 2,
) -> list[ContentGroupSummary]:
    org_uuid = UUID(organization_id)
    campfire_ids_subquery = (
        select(CampfireContentGroup.campfire_id)
        .join(Campfire, Campfire.id == CampfireContentGroup.campfire_id)
        .where(Campfire.organization_id == org_uuid)
        .where(Campfire.is_archived == False)  # noqa: E712
        .where(CampfireContentGroup.content_group_id == UUID(content_group_id))
    )
    stmt = (
        select(ContentGroupSummary)
        .join(ContentGroup, ContentGroup.id == ContentGroupSummary.content_group_id)
        .join(CampfireContentGroup, CampfireContentGroup.content_group_id == ContentGroupSummary.content_group_id)
        .join(Campfire, Campfire.id == CampfireContentGroup.campfire_id)
        .where(Campfire.organization_id == org_uuid)
        .where(ContentGroup.organization_id == org_uuid)
        .where(ContentGroupSummary.organization_id == org_uuid)
        .where(CampfireContentGroup.campfire_id.in_(campfire_ids_subquery))
        .where(CampfireContentGroup.content_group_id != UUID(content_group_id))
        .where(Campfire.is_archived == False)  # noqa: E712
        .order_by(ContentGroupSummary.summarized_through_at.desc())
        .limit(summary_limit_per_group)
    )
    async with get_session(organization_id=organization_id) as session:
        rows = await session.execute(stmt)
        return list(rows.scalars().all())
