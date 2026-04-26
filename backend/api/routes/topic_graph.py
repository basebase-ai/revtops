from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth_middleware import AuthContext, require_global_admin
from services.topic_graph import get_node_evidence, get_topic_graph_snapshot
from workers.tasks.topic_graph import rebuild_org_date_range

router = APIRouter()
logger = logging.getLogger(__name__)


class RebuildRequest(BaseModel):
    organization_id: str
    start_date: str | None = None
    end_date: str | None = None


@router.get("/{organization_id}/{graph_date}")
async def get_graph_snapshot(
    organization_id: str,
    graph_date: str,
    auth: AuthContext = Depends(require_global_admin),
) -> dict[str, Any]:
    d = date.fromisoformat(graph_date)
    snapshot = await get_topic_graph_snapshot(organization_id, d)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Graph snapshot not found")
    return {
        "organization_id": organization_id,
        "graph_date": d.isoformat(),
        "status": snapshot.status,
        "graph": snapshot.graph_payload,
        "run_metadata": snapshot.run_metadata,
    }


@router.get("/{organization_id}/{graph_date}/nodes/{node_id}/evidence")
async def get_graph_node_evidence(
    organization_id: str,
    graph_date: str,
    node_id: str,
    auth: AuthContext = Depends(require_global_admin),
) -> dict[str, Any]:
    d = date.fromisoformat(graph_date)
    snippets = await get_node_evidence(organization_id, d, node_id)
    return {
        "organization_id": organization_id,
        "graph_date": d.isoformat(),
        "node_id": node_id,
        "snippets": snippets,
    }


@router.post("/rebuild")
async def enqueue_graph_rebuild(
    req: RebuildRequest,
    auth: AuthContext = Depends(require_global_admin),
) -> dict[str, Any]:
    start = date.fromisoformat(req.start_date) if req.start_date else datetime.now(timezone.utc).date()
    end = date.fromisoformat(req.end_date) if req.end_date else start
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")
    day_count = (end - start).days + 1
    if day_count > 30:
        raise HTTPException(status_code=400, detail="Date range must be <= 30 days")
    logger.info("topic_graph.stage=api_enqueue org_id=%s start=%s end=%s by=%s", req.organization_id, start, end, auth.user_id)
    task = rebuild_org_date_range.delay(req.organization_id, start.isoformat(), end.isoformat())
    return {
        "status": "queued",
        "organization_id": req.organization_id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "task_id": str(task.id),
        "sequential": True,
    }
