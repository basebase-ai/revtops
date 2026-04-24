"""Celery tasks for generating content-group summaries."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from workers.celery_app import celery_app
from workers.run_async import run_async

logger = logging.getLogger(__name__)


async def _generate_for_org_provider_sync(
    organization_id: str,
    provider: str,
    content_group_ids: list[str],
    sync_through_iso: str,
) -> dict[str, Any]:
    from services.content_group_summary import generate_content_group_summary_for_sync

    sync_through = datetime.fromisoformat(sync_through_iso)
    if sync_through.tzinfo is None:
        sync_through = sync_through.replace(tzinfo=UTC)

    generated: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = []
    for content_group_id in content_group_ids:
        result = await generate_content_group_summary_for_sync(
            organization_id=organization_id,
            content_group_id=content_group_id,
            sync_through_at=sync_through,
        )
        results.append(result)
        if result.get("status") == "generated":
            generated += 1
        elif result.get("status") == "failed":
            failed += 1

    return {
        "status": "completed",
        "organization_id": organization_id,
        "provider": provider,
        "content_group_count": len(content_group_ids),
        "generated": generated,
        "failed": failed,
        "results": results,
    }


@celery_app.task(
    bind=True,
    name="workers.tasks.content_group_summaries.generate_for_org_provider_sync",
    max_retries=2,
    default_retry_delay=30,
)
def generate_for_org_provider_sync(
    self: Any,
    organization_id: str,
    provider: str,
    content_group_ids: list[str],
    sync_through_iso: str | None = None,
) -> dict[str, Any]:
    logger.info(
        "Task %s: generate_for_org_provider_sync org=%s provider=%s groups=%d",
        self.request.id,
        organization_id,
        provider,
        len(content_group_ids),
    )
    through = sync_through_iso or datetime.now(UTC).isoformat()
    return run_async(_generate_for_org_provider_sync(organization_id, provider, content_group_ids, through))
