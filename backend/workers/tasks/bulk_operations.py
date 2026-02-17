"""
Celery tasks for the ``foreach`` tool's distributed tool-execution path.

Architecture
------------
``bulk_tool_run_coordinator``
    Reads the item list (from inline data or a SQL query), fans out one
    ``bulk_tool_run_item`` task per item via ``celery.group()``, then updates
    the BulkOperation record when all items are processed.

``bulk_tool_run_item``
    Acquires a rate-limit token, calls ``execute_tool`` with the rendered
    params, writes a ``BulkOperationResult`` row, and increments Redis
    counters.

Both tasks run on the ``enrichment`` queue.  Progress polling is handled
inline by ``_poll_bulk_operation`` in ``agents/tools.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from celery import group as celery_group
from sqlalchemy import select, func as sa_func, text

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# How often the coordinator broadcasts progress via WebSocket (seconds)
_PROGRESS_BROADCAST_INTERVAL: float = 5.0

# Maximum items that can be processed in a single bulk operation
MAX_BULK_ITEMS: int = 50_000

# Redis key helpers
def _redis_key(operation_id: str, field: str) -> str:
    return f"bulk_op:{operation_id}:{field}"


def run_async(coro: Any) -> Any:
    """Run an async function in a sync Celery task context."""
    from models.database import dispose_engine

    dispose_engine()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _mark_operation_failed(operation_id: str, organization_id: str, error: str) -> None:
    """Mark a bulk operation as failed in the DB so the foreach poller can detect it."""
    from models.database import get_session
    from models.bulk_operation import BulkOperation

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(BulkOperation).where(BulkOperation.id == UUID(operation_id))
        )
        op: BulkOperation | None = result.scalar_one_or_none()
        if op:
            op.status = "failed"
            op.error = error[:2000]  # Truncate long tracebacks
            op.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info("[BulkOp] Marked operation %s as failed: %s", operation_id[:8], error[:200])


def _render_template(template: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """
    Render a params_template dict by substituting ``{{key}}`` placeholders
    with values from *item*.

    Handles nested string values and leaves non-string values untouched.
    """
    rendered: dict[str, Any] = {}
    for key, value in template.items():
        if isinstance(value, str):
            # Replace all {{field}} placeholders
            def _replace(match: re.Match[str]) -> str:
                field_name: str = match.group(1).strip()
                item_value: Any = item.get(field_name, "")
                return str(item_value) if item_value is not None else ""

            rendered[key] = re.sub(r"\{\{(\w+)\}\}", _replace, value)
        elif isinstance(value, dict):
            rendered[key] = _render_template(value, item)
        else:
            rendered[key] = value
    return rendered


# ---------------------------------------------------------------------------
# Per-item worker task
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="workers.tasks.bulk_operations.bulk_tool_run_item",
    acks_late=True,          # Re-deliver on worker crash
    reject_on_worker_lost=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
    soft_time_limit=120,     # 2 min per item (generous)
    time_limit=150,          # Hard kill at 2.5 min
)
def bulk_tool_run_item(
    self: Any,
    *,
    operation_id: str,
    item_index: int,
    item_data: dict[str, Any],
    tool_name: str,
    rendered_params: dict[str, Any],
    organization_id: str,
    user_id: Optional[str],
    rate_limit_key: str,
    rate_limit_per_minute: int,
    redis_url: str,
) -> dict[str, Any]:
    """Process a single item: rate-limit → execute_tool → persist result."""
    return run_async(
        _bulk_tool_run_item_async(
            operation_id=operation_id,
            item_index=item_index,
            item_data=item_data,
            tool_name=tool_name,
            rendered_params=rendered_params,
            organization_id=organization_id,
            user_id=user_id,
            rate_limit_key=rate_limit_key,
            rate_limit_per_minute=rate_limit_per_minute,
            redis_url=redis_url,
        )
    )


async def _bulk_tool_run_item_async(
    *,
    operation_id: str,
    item_index: int,
    item_data: dict[str, Any],
    tool_name: str,
    rendered_params: dict[str, Any],
    organization_id: str,
    user_id: Optional[str],
    rate_limit_key: str,
    rate_limit_per_minute: int,
    redis_url: str,
) -> dict[str, Any]:
    """Async implementation of per-item processing."""
    import redis.asyncio as aioredis
    from models.database import get_session
    from models.bulk_operation import BulkOperationResult
    from agents.tools import execute_tool
    from workers.rate_limiter import RedisRateLimiter

    # Retry config for transient errors (rate-limit 429, server errors, etc.)
    max_retries: int = 5
    base_backoff: float = 5.0  # seconds

    success: bool = False
    error_msg: Optional[str] = None
    result_data: Optional[dict[str, Any]] = None

    for attempt in range(max_retries + 1):
        # Acquire rate-limit token before each attempt
        limiter = RedisRateLimiter(
            redis_url=redis_url,
            key=rate_limit_key,
            rate_per_minute=rate_limit_per_minute,
        )
        try:
            acquired: bool = await limiter.acquire(timeout=300)
            if not acquired:
                raise RuntimeError("Rate-limit token acquisition timed out (300s)")
        finally:
            await limiter.close()

        # Execute the tool
        try:
            result_data = await execute_tool(
                tool_name=tool_name,
                tool_input=rendered_params,
                organization_id=organization_id,
                user_id=user_id,
            )
            # Treat result with "error" key as failure
            if isinstance(result_data, dict) and "error" in result_data:
                error_str: str = str(result_data["error"])
                # Retry on rate-limit (429) or server errors (5xx)
                if attempt < max_retries and ("429" in error_str or "rate limit" in error_str.lower() or "500" in error_str or "502" in error_str or "503" in error_str):
                    backoff: float = base_backoff * (2 ** attempt)
                    logger.warning(
                        "[BulkOp] Item %d transient error (attempt %d/%d), retrying in %.1fs: %s",
                        item_index, attempt + 1, max_retries, backoff, error_str[:200],
                    )
                    await asyncio.sleep(backoff)
                    continue
                error_msg = error_str
                success = False
            else:
                success = True
        except Exception as exc:
            error_msg = str(exc)
            success = False
            logger.error(
                "[BulkOp] Item %d failed for op %s: %s",
                item_index, operation_id[:8], error_msg,
            )
        break  # Exit retry loop on success or non-retryable error

    # Persist result row
    async with get_session(organization_id=organization_id) as session:
        result_row = BulkOperationResult(
            bulk_operation_id=UUID(operation_id),
            item_index=item_index,
            item_data=item_data,
            result_data=result_data,
            success=success,
            error=error_msg,
        )
        session.add(result_row)
        await session.commit()

    # Increment Redis progress counters (atomic) and check if we're the last item
    r: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        pipe = r.pipeline()
        pipe.incr(_redis_key(operation_id, "completed"))
        if success:
            pipe.incr(_redis_key(operation_id, "succeeded"))
        else:
            pipe.incr(_redis_key(operation_id, "failed"))
        counters: list[int] = await pipe.execute()
        new_completed: int = counters[0]  # result of INCR completed

        # If this was the last item, finalise the operation
        total_raw: str | None = await r.get(_redis_key(operation_id, "total"))
        total_items: int = int(total_raw) if total_raw else 0

        if total_items > 0 and new_completed >= total_items:
            logger.info(
                "[BulkOp] Last item done for operation %s — finalising",
                operation_id[:8],
            )
            succeeded_raw: str | None = await r.get(_redis_key(operation_id, "succeeded"))
            failed_raw: str | None = await r.get(_redis_key(operation_id, "failed"))
            final_succeeded: int = int(succeeded_raw) if succeeded_raw else 0
            final_failed: int = int(failed_raw) if failed_raw else 0

            async with get_session(organization_id=organization_id) as session:
                from models.bulk_operation import BulkOperation
                op_result = await session.execute(
                    select(BulkOperation).where(BulkOperation.id == UUID(operation_id))
                )
                op: BulkOperation = op_result.scalar_one()
                op.status = "completed"
                op.completed_items = new_completed
                op.succeeded_items = final_succeeded
                op.failed_items = final_failed
                op.completed_at = datetime.now(timezone.utc)
                await session.commit()

            # Clean up Redis keys
            for field in ("completed", "succeeded", "failed", "total"):
                await r.delete(_redis_key(operation_id, field))
    finally:
        await r.close()

    return {
        "item_index": item_index,
        "success": success,
        "error": error_msg,
    }


# ---------------------------------------------------------------------------
# Coordinator task
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="workers.tasks.bulk_operations.bulk_tool_run_coordinator",
    soft_time_limit=4 * 3600,   # 4 hours soft
    time_limit=4 * 3600 + 300,  # 4 hours + 5 min hard
)
def bulk_tool_run_coordinator(
    self: Any,
    *,
    operation_id: str,
    organization_id: str,
    user_id: Optional[str],
) -> dict[str, Any]:
    """Fan out per-item tasks and poll progress until done."""
    try:
        return run_async(
            _bulk_tool_run_coordinator_async(
                celery_task=self,
                operation_id=operation_id,
                organization_id=organization_id,
                user_id=user_id,
            )
        )
    except Exception as exc:
        # Mark the operation as failed so the foreach poller doesn't hang
        logger.error("[BulkOp] Coordinator failed for %s: %s", operation_id[:8], exc)
        try:
            run_async(_mark_operation_failed(operation_id, organization_id, str(exc)))
        except Exception as mark_err:
            logger.error("[BulkOp] Failed to mark operation %s as failed: %s", operation_id[:8], mark_err)
        raise


async def _bulk_tool_run_coordinator_async(
    *,
    celery_task: Any,
    operation_id: str,
    organization_id: str,
    user_id: Optional[str],
) -> dict[str, Any]:
    """Async coordinator: load items, fan out, poll progress, broadcast."""
    import redis.asyncio as aioredis
    from models.database import get_session
    from models.bulk_operation import BulkOperation, BulkOperationResult
    from config import settings

    redis_url: str = settings.REDIS_URL

    # --- Load the operation record ---
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(BulkOperation).where(BulkOperation.id == UUID(operation_id))
        )
        operation: Optional[BulkOperation] = result.scalar_one_or_none()
        if not operation:
            raise ValueError(f"BulkOperation {operation_id} not found")

        tool_name: str = operation.tool_name
        params_template: dict[str, Any] = operation.params_template
        items_query: Optional[str] = operation.items_query
        rate_limit_per_minute: int = operation.rate_limit_per_minute
        conversation_id: Optional[str] = operation.conversation_id
        tool_call_id: Optional[str] = operation.tool_call_id
        op_name: str = operation.operation_name

    # --- Fetch items ---
    items: list[dict[str, Any]] = []

    if items_query:
        # Run the SQL query to get items
        async with get_session(organization_id=organization_id) as session:
            # Safety: only SELECT allowed
            stripped: str = items_query.strip().upper()
            if not stripped.startswith("SELECT"):
                raise ValueError("items_query must be a SELECT statement")

            raw = await session.execute(text(items_query))
            rows = raw.mappings().all()
            items = [dict(row) for row in rows]

            # Stringify UUIDs for JSON serialisation
            for item in items:
                for k, v in item.items():
                    if isinstance(v, UUID):
                        item[k] = str(v)
                    elif isinstance(v, datetime):
                        item[k] = v.isoformat()
    else:
        # Read inline items from Redis (stored by _foreach_tool)
        r_items: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
        try:
            raw_items: Optional[str] = await r_items.get(f"bulk_op:{operation_id}:items")
            if raw_items:
                items = json.loads(raw_items)
                # Clean up — no longer needed
                await r_items.delete(f"bulk_op:{operation_id}:items")
            else:
                logger.warning("[BulkOp] No items_query and no inline items found for %s", operation_id[:8])
        finally:
            await r_items.close()

    if len(items) > MAX_BULK_ITEMS:
        items = items[:MAX_BULK_ITEMS]
        logger.warning(
            "[BulkOp] Truncated items to %d for operation %s",
            MAX_BULK_ITEMS, operation_id[:8],
        )

    total_items: int = len(items)
    if total_items == 0:
        # Nothing to do
        async with get_session(organization_id=organization_id) as session:
            result = await session.execute(
                select(BulkOperation).where(BulkOperation.id == UUID(operation_id))
            )
            op = result.scalar_one()
            op.status = "completed"
            op.total_items = 0
            op.completed_at = datetime.now(timezone.utc)
            await session.commit()
        return {"status": "completed", "total_items": 0, "message": "No items matched the query."}

    # --- Check for already-completed items (resume support) ---
    completed_indices: set[int] = set()
    async with get_session(organization_id=organization_id) as session:
        existing = await session.execute(
            select(BulkOperationResult.item_index).where(
                BulkOperationResult.bulk_operation_id == UUID(operation_id)
            )
        )
        completed_indices = {row[0] for row in existing.all()}

    remaining_items: list[tuple[int, dict[str, Any]]] = [
        (i, item) for i, item in enumerate(items) if i not in completed_indices
    ]

    already_done: int = len(completed_indices)
    remaining_count: int = len(remaining_items)

    logger.info(
        "[BulkOp] Operation %s: %d total, %d already done, %d remaining",
        operation_id[:8], total_items, already_done, remaining_count,
    )

    # --- Update operation record ---
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(BulkOperation).where(BulkOperation.id == UUID(operation_id))
        )
        op = result.scalar_one()
        op.status = "running"
        op.total_items = total_items
        op.completed_items = already_done
        op.started_at = op.started_at or datetime.now(timezone.utc)
        op.celery_task_id = celery_task.request.id
        await session.commit()

    # --- Initialise Redis progress counters ---
    r: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await r.set(_redis_key(operation_id, "total"), total_items)
        await r.set(_redis_key(operation_id, "completed"), already_done)
        # Re-count succeeded/failed from DB for accurate resume
        async with get_session(organization_id=organization_id) as session:
            agg = await session.execute(
                select(
                    sa_func.count().filter(BulkOperationResult.success == True).label("ok"),
                    sa_func.count().filter(BulkOperationResult.success == False).label("fail"),
                ).where(BulkOperationResult.bulk_operation_id == UUID(operation_id))
            )
            row = agg.one()
            await r.set(_redis_key(operation_id, "succeeded"), row.ok)
            await r.set(_redis_key(operation_id, "failed"), row.fail)
    finally:
        await r.close()

    # --- Fan out per-item tasks ---
    rate_limit_key: str = f"bulk_op:{operation_id}"

    task_signatures = [
        bulk_tool_run_item.s(
            operation_id=operation_id,
            item_index=idx,
            item_data=item,
            tool_name=tool_name,
            rendered_params=_render_template(params_template, item),
            organization_id=organization_id,
            user_id=user_id,
            rate_limit_key=rate_limit_key,
            rate_limit_per_minute=rate_limit_per_minute,
            redis_url=redis_url,
        )
        for idx, item in remaining_items
    ]

    if task_signatures:
        job = celery_group(task_signatures)
        job.apply_async()
        logger.info(
            "[BulkOp] Dispatched %d tasks for operation %s",
            len(task_signatures), operation_id[:8],
        )

    # Coordinator returns immediately after dispatch.
    # Progress polling is handled by _poll_bulk_operation in agents/tools.py
    # (called inline by the foreach tool).  Each bulk_tool_run_item updates
    # Redis counters; the last item to finish checks if completed == total
    # and flips the DB status to "completed".
    logger.info(
        "[BulkOp] Coordinator done for operation %s — dispatched %d items, returning",
        operation_id[:8], remaining_count,
    )

    return {
        "status": "running",
        "operation_id": operation_id,
        "total_items": total_items,
        "dispatched": remaining_count,
    }
