"""Workflow execution pause controls for emergency admin operations."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

import redis.asyncio as aioredis

from config import get_redis_connection_kwargs, settings

logger = logging.getLogger(__name__)

WORKFLOW_EXECUTION_PAUSE_KEY = "admin:workflow_execution_pause_until"


async def pause_workflow_execution_for_seconds(*, seconds: int) -> datetime:
    """Pause new workflow execution attempts for a fixed duration in seconds."""
    now_utc = datetime.now(timezone.utc)
    pause_until = now_utc.timestamp() + max(seconds, 0)
    ttl_seconds = max(seconds, 1)
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        **get_redis_connection_kwargs(decode_responses=True),
    )
    try:
        async with redis_client:
            await redis_client.set(
                WORKFLOW_EXECUTION_PAUSE_KEY,
                str(pause_until),
                ex=ttl_seconds,
            )
    except Exception:
        logger.exception("Failed to set workflow execution pause flag")
        raise
    return datetime.fromtimestamp(pause_until, tz=timezone.utc)


async def get_workflow_execution_pause_until() -> datetime | None:
    """Return the pause-until timestamp when workflow execution is paused."""
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        **get_redis_connection_kwargs(decode_responses=True),
    )
    try:
        async with redis_client:
            raw_value = await redis_client.get(WORKFLOW_EXECUTION_PAUSE_KEY)
    except Exception:
        logger.exception("Failed to read workflow execution pause flag")
        return None

    if raw_value is None:
        return None

    try:
        pause_until_epoch = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Workflow execution pause flag had non-numeric value=%r",
            raw_value,
        )
        return None
    pause_until = datetime.fromtimestamp(pause_until_epoch, tz=timezone.utc)
    if pause_until <= datetime.now(timezone.utc):
        return None
    return pause_until


async def is_workflow_execution_paused() -> bool:
    """Whether new workflow execution attempts should be blocked."""
    return await get_workflow_execution_pause_until() is not None

