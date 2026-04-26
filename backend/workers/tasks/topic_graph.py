from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_backend_dir = Path(__file__).resolve().parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from workers.celery_app import celery_app
from workers.run_async import run_async

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="workers.tasks.topic_graph.generate_daily_org_graph")
def generate_daily_org_graph(self: Any, organization_id: str, graph_date_iso: str) -> dict[str, Any]:
    logger.info("topic_graph.task=generate org_id=%s graph_date=%s task_id=%s", organization_id, graph_date_iso, self.request.id)

    async def _run() -> dict[str, Any]:
        from services.topic_graph import generate_topic_graph_for_org_day

        return await generate_topic_graph_for_org_day(organization_id, date.fromisoformat(graph_date_iso))

    return run_async(_run())


@celery_app.task(bind=True, name="workers.tasks.topic_graph.rebuild_org_date_range")
def rebuild_org_date_range(self: Any, organization_id: str, start_date_iso: str, end_date_iso: str) -> dict[str, Any]:
    logger.info(
        "topic_graph.task=rebuild_range org_id=%s start=%s end=%s task_id=%s",
        organization_id,
        start_date_iso,
        end_date_iso,
        self.request.id,
    )

    async def _run() -> dict[str, Any]:
        from services.topic_graph import generate_topic_graph_for_org_day, iter_date_range

        start_date = date.fromisoformat(start_date_iso)
        end_date = date.fromisoformat(end_date_iso)
        days = iter_date_range(start_date, end_date)
        results: list[dict[str, Any]] = []
        for day in days:  # sequential by day (product decision)
            results.append(await generate_topic_graph_for_org_day(organization_id, day))
        return {"status": "completed", "days": len(days), "results": results}

    return run_async(_run())


@celery_app.task(bind=True, name="workers.tasks.topic_graph.generate_daily_all_orgs")
def generate_daily_all_orgs(self: Any, graph_date_iso: str | None = None) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    day = date.fromisoformat(graph_date_iso) if graph_date_iso else today
    logger.info("topic_graph.task=nightly_dispatch date=%s task_id=%s", day.isoformat(), self.request.id)

    async def _run() -> dict[str, Any]:
        from services.topic_graph import cleanup_topic_graph_retention, list_all_organization_ids

        org_ids = await list_all_organization_ids()
        task_ids: list[str] = []
        for org_id in org_ids:
            r = generate_daily_org_graph.delay(org_id, day.isoformat())
            task_ids.append(str(r.id))
        removed = await cleanup_topic_graph_retention()
        return {"status": "dispatched", "orgs": len(org_ids), "child_task_ids": task_ids, "retention_deleted": removed}

    return run_async(_run())
