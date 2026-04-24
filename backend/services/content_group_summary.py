from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select

from models.activity import Activity
from models.content_group import ContentGroup, ContentGroupSummary
from models.database import get_session
from services.llm_provider import get_adapter, resolve_llm_config

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
_METRICS: dict[str, int] = {
    "content_group_summary_generated_total": 0,
    "content_group_summary_skipped_empty_total": 0,
    "content_group_summary_failed_total": 0,
}


async def get_last_summary_watermark(content_group_id: str, organization_id: str) -> datetime | None:
    stmt = (
        select(ContentGroupSummary.summarized_through_at)
        .where(ContentGroupSummary.content_group_id == UUID(content_group_id))
        .order_by(ContentGroupSummary.summarized_through_at.desc())
        .limit(1)
    )
    async with get_session(organization_id=organization_id) as session:
        return (await session.execute(stmt)).scalar_one_or_none()


def _select_messages_stmt(
    organization_id: str,
    content_group: ContentGroup,
    since: datetime | None,
    through: datetime,
    limit: int,
) -> Select[tuple[Activity]]:
    stmt = (
        select(Activity)
        .where(Activity.organization_id == UUID(organization_id))
        .where(Activity.source_system == content_group.platform)
        .where(Activity.custom_fields["channel_id"].astext == content_group.external_group_id)
        .where(Activity.activity_date.is_not(None))
        .where(Activity.activity_date <= through)
        .order_by(Activity.activity_date.asc())
        .limit(limit)
    )
    if content_group.external_thread_id:
        stmt = stmt.where(Activity.custom_fields["thread_ts"].astext == content_group.external_thread_id)
    if since is not None:
        stmt = stmt.where(Activity.activity_date > since)
    return stmt


async def select_unsummarized_messages(
    organization_id: str,
    content_group_id: str,
    since: datetime | None,
    through: datetime,
    limit: int = 200,
) -> list[Activity]:
    async with get_session(organization_id=organization_id) as session:
        group = await session.get(ContentGroup, UUID(content_group_id))
        if group is None:
            return []
        rows = await session.execute(_select_messages_stmt(organization_id, group, since, through, limit))
        activities = list(rows.scalars().all())
        logger.info(
            "[content_group.summary_candidates] org_id=%s content_group_id=%s candidate_count=%d since=%s through=%s",
            organization_id,
            content_group_id,
            len(activities),
            since.isoformat() if since else None,
            through.isoformat(),
        )
        return activities


def build_summary_prompt(transcript_chunk: str, metadata: dict[str, Any]) -> str:
    return (
        "Summarize this Slack/Teams channel window for AI context reuse. "
        "Focus on decisions, blockers, owners, and concrete next steps. "
        "Be concise, factual, and avoid speculation.\n\n"
        f"Metadata: {metadata}\n\nTranscript:\n{transcript_chunk}"
    )


async def _with_retry(coro_factory: Any, *, attempts: int = 3) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            msg = str(exc).lower()
            retriable = "deadlock" in msg or "serialization" in msg
            if not retriable or attempt == attempts:
                raise
            sleep_s = (0.15 * (2 ** (attempt - 1))) + random.random() * 0.05
            await asyncio.sleep(sleep_s)


async def generate_content_group_summary_for_sync(
    *,
    organization_id: str,
    content_group_id: str,
    sync_through_at: datetime,
) -> dict[str, Any]:
    try:
        watermark = await get_last_summary_watermark(content_group_id, organization_id)
        candidates = await select_unsummarized_messages(
            organization_id=organization_id,
            content_group_id=content_group_id,
            since=watermark,
            through=sync_through_at,
        )
        if not candidates:
            _METRICS["content_group_summary_skipped_empty_total"] += 1
            return {"status": "skipped", "reason": "empty", "content_group_id": content_group_id}

        transcript_lines = []
        for row in candidates:
            when = row.activity_date.isoformat() if row.activity_date else ""
            who = (row.custom_fields or {}).get("user_id") or "unknown"
            body = (row.description or row.subject or "").strip().replace("\n", " ")
            transcript_lines.append(f"[{when}] {who}: {body}")
        transcript = "\n".join(transcript_lines)

        llm_config = await resolve_llm_config(organization_id)
        adapter = get_adapter(llm_config)
        prompt_meta = {
            "content_group_id": content_group_id,
            "message_count": len(candidates),
            "first_message_id": candidates[0].source_id,
            "last_message_id": candidates[-1].source_id,
        }
        prompt = build_summary_prompt(transcript, prompt_meta)

        t0 = time.perf_counter()
        response = await adapter.send_message(
            system_prompt="You write compact channel summaries for downstream context injection.",
            messages=[{"role": "user", "content": prompt}],
            model=llm_config.workflow_model or llm_config.primary_model,
            max_tokens=600,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        summary_text = (response or "").strip()

        logger.info(
            "[content_group.summary_llm] org_id=%s content_group_id=%s model=%s prompt_version=%s llm_latency_ms=%d",
            organization_id,
            content_group_id,
            llm_config.workflow_model or llm_config.primary_model,
            PROMPT_VERSION,
            elapsed_ms,
        )

        async def _write() -> dict[str, Any]:
            async with get_session(organization_id=organization_id) as session:
                summarized_through_at = candidates[-1].activity_date or sync_through_at
                row = ContentGroupSummary(
                    organization_id=UUID(organization_id),
                    content_group_id=UUID(content_group_id),
                    summary_text=summary_text,
                    summary_json=None,
                    first_message_external_id=candidates[0].source_id,
                    last_message_external_id=candidates[-1].source_id,
                    first_message_at=candidates[0].activity_date or sync_through_at,
                    last_message_at=candidates[-1].activity_date or sync_through_at,
                    summarized_through_at=summarized_through_at,
                    message_count=len(candidates),
                    model=llm_config.workflow_model or llm_config.primary_model,
                    prompt_version=PROMPT_VERSION,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return {
                    "status": "generated",
                    "summary_id": str(row.id),
                    "content_group_id": content_group_id,
                    "message_count": row.message_count,
                    "first_message_external_id": row.first_message_external_id,
                    "last_message_external_id": row.last_message_external_id,
                }

        out = await _with_retry(_write)
        _METRICS["content_group_summary_generated_total"] += 1
        logger.info("[content_group.summary_write_success] %s", out)
        return out
    except Exception as exc:
        _METRICS["content_group_summary_failed_total"] += 1
        logger.exception(
            "[content_group.summary_failed] org_id=%s content_group_id=%s error=%s",
            organization_id,
            content_group_id,
            exc,
        )
        return {"status": "failed", "error": str(exc), "content_group_id": content_group_id}
