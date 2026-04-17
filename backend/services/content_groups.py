from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models.content_group import ContentGroup, ContentGroupSummary
from models.database import get_session

logger = logging.getLogger(__name__)


def _normalize_content_group_key(message_ctx: dict[str, Any], platform: str) -> dict[str, str | None] | None:
    # Slack docs: channel_id is stable channel identifier; thread_ts is stable root
    # timestamp for the reply chain. Teams inbound adapters expose workspace_id
    # (tenant/team), channel_id/chat_id, and reply_to_id for thread reply chain IDs.
    workspace_id = str(message_ctx.get("workspace_id") or "").strip()
    external_group_id = str(message_ctx.get("channel_id") or message_ctx.get("chat_id") or "").strip()
    if not workspace_id or not external_group_id:
        return None

    external_thread_id = (
        str(message_ctx.get("thread_id") or message_ctx.get("thread_ts") or message_ctx.get("reply_to_id") or "").strip() or None
    )
    name = str(message_ctx.get("channel_name") or message_ctx.get("chat_name") or "").strip() or None
    return {
        "platform": platform,
        "workspace_id": workspace_id,
        "external_group_id": external_group_id,
        "external_thread_id": external_thread_id,
        "name": name,
    }


async def resolve_content_group_from_message(
    message_ctx: dict[str, Any], organization_id: str, platform: str
) -> ContentGroup | None:
    key = _normalize_content_group_key(message_ctx, platform)
    if key is None:
        logger.warning(
            "[content_group.resolve] missing_content_group_keys org_id=%s platform=%s ctx_keys=%s",
            organization_id,
            platform,
            sorted(message_ctx.keys()),
        )
        return None

    org_uuid = UUID(organization_id)
    async with get_session(organization_id=organization_id) as session:
        group = None
        # Nullable external_thread_id means UNIQUE doesn't prevent duplicates for NULL.
        # Resolve the non-threaded row first to keep channel-level groups stable.
        if key["external_thread_id"] is None:
            existing_stmt = (
                select(ContentGroup)
                .where(ContentGroup.organization_id == org_uuid)
                .where(ContentGroup.platform == key["platform"])
                .where(ContentGroup.workspace_id == key["workspace_id"])
                .where(ContentGroup.external_group_id == key["external_group_id"])
                .where(ContentGroup.external_thread_id.is_(None))
                .limit(1)
            )
            group = (await session.execute(existing_stmt)).scalar_one_or_none()
            if group is not None:
                group.name = key["name"]
                group.is_active = True
                group.updated_at = datetime.now(UTC)

        if group is None:
            insert_stmt = pg_insert(ContentGroup).values(
                organization_id=org_uuid,
                platform=key["platform"],
                workspace_id=key["workspace_id"],
                external_group_id=key["external_group_id"],
                external_thread_id=key["external_thread_id"],
                name=key["name"],
                is_active=True,
            )
            upsert_stmt = insert_stmt.on_conflict_do_update(
                constraint="uq_content_groups_key",
                set_={
                    "name": key["name"],
                    "updated_at": datetime.now(UTC),
                    "is_active": True,
                },
            ).returning(ContentGroup.id)

            group_id = (await session.execute(upsert_stmt)).scalar_one()
            group = await session.get(ContentGroup, group_id)
        await session.commit()

    logger.info(
        "[content_group.resolve] org_id=%s platform=%s workspace_id=%s external_group_id=%s external_thread_id=%s content_group_id=%s",
        organization_id,
        platform,
        key["workspace_id"],
        key["external_group_id"],
        key["external_thread_id"],
        str(group.id) if group else None,
    )
    return group


async def associate_conversation_to_content_group(
    organization_id: str,
    conversation_id: str,
    content_group_id: str,
) -> None:
    from models.conversation import Conversation

    async with get_session(organization_id=organization_id) as session:
        conv = await session.get(Conversation, UUID(conversation_id))
        if conv is None:
            return
        conv.content_group_id = UUID(content_group_id)
        await session.commit()


async def list_recent_summaries(
    content_group_id: str,
    limit: int = 4,
    max_age_hours: int = 72,
    organization_id: str | None = None,
) -> list[ContentGroupSummary]:
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    stmt = (
        select(ContentGroupSummary)
        .where(ContentGroupSummary.content_group_id == UUID(content_group_id))
        .where(ContentGroupSummary.summarized_through_at >= cutoff)
        .order_by(ContentGroupSummary.summarized_through_at.desc())
        .limit(limit)
    )
    async with get_session(organization_id=organization_id) as session:
        rows = await session.execute(stmt)
        return list(rows.scalars().all())
