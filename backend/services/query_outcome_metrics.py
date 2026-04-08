"""Rolling query outcome metrics for messenger-originated turns."""
from __future__ import annotations

import logging
import time

import redis.asyncio as aioredis

from config import get_redis_connection_kwargs, settings
from services.incident_throttling import clear_incident_failure, evaluate_incident_creation
from services.pagerduty import create_pagerduty_incident

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 30 * 60
_SUCCESS_KEY = "monitoring:query_outcomes:success"
_FAILURE_KEY = "monitoring:query_outcomes:failure"
_QUERY_SUCCESS_CHECK_NAME = "Rolling Query Success"
_QUERY_SUCCESS_INCIDENT_THRESHOLD_PCT = 25.0


async def get_query_outcome_window_stats() -> dict[str, float | int]:
    """Return rolling 30-minute query outcome stats from Redis."""
    timestamp = int(time.time())
    window_start = timestamp - _WINDOW_SECONDS
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        **get_redis_connection_kwargs(),
    )

    async with redis_client:
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(_SUCCESS_KEY, "-inf", window_start)
        pipe.zremrangebyscore(_FAILURE_KEY, "-inf", window_start)
        pipe.zcard(_SUCCESS_KEY)
        pipe.zcard(_FAILURE_KEY)
        _, _, success_count, failure_count = await pipe.execute()

    success_total = int(success_count or 0)
    failure_total = int(failure_count or 0)
    total = success_total + failure_total
    success_pct = ((success_total / total) * 100.0) if total else 100.0
    return {
        "window_seconds": _WINDOW_SECONDS,
        "success_count": success_total,
        "failure_count": failure_total,
        "total_count": total,
        "success_rate_pct": success_pct,
    }


async def record_query_outcome(*, platform: str, was_success: bool) -> None:
    """Record one query outcome and maintain a rolling 30-minute success pct."""
    timestamp = int(time.time())
    score = float(timestamp)
    bucket_key = _SUCCESS_KEY if was_success else _FAILURE_KEY
    member = f"{timestamp}:{platform}:{'ok' if was_success else 'fail'}:{time.time_ns()}"

    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        **get_redis_connection_kwargs(),
    )

    async with redis_client:
        pipe = redis_client.pipeline()
        pipe.zadd(bucket_key, {member: score})
        pipe.expire(bucket_key, _WINDOW_SECONDS * 2)
        await pipe.execute()

    stats = await get_query_outcome_window_stats()

    logger.info(
        "Rolling query outcomes window_seconds=%s platform=%s was_success=%s "
        "success_count=%s failure_count=%s success_pct=%.2f",
        stats["window_seconds"],
        platform,
        was_success,
        stats["success_count"],
        stats["failure_count"],
        stats["success_rate_pct"],
    )
    await _maybe_raise_query_success_incident(platform=platform, stats=stats)


async def _maybe_raise_query_success_incident(
    *,
    platform: str,
    stats: dict[str, float | int],
) -> None:
    """Raise/suppress incident when rolling success percentage crosses threshold."""
    success_rate_pct = float(stats["success_rate_pct"])
    if success_rate_pct > _QUERY_SUCCESS_INCIDENT_THRESHOLD_PCT:
        logger.info(
            "Rolling query success recovered platform=%s success_pct=%.2f threshold_pct=%.2f",
            platform,
            success_rate_pct,
            _QUERY_SUCCESS_INCIDENT_THRESHOLD_PCT,
        )
        await clear_incident_failure(_QUERY_SUCCESS_CHECK_NAME)
        return

    should_create, reason = await evaluate_incident_creation(_QUERY_SUCCESS_CHECK_NAME)
    logger.warning(
        "Rolling query success degraded platform=%s success_pct=%.2f threshold_pct=%.2f "
        "total_count=%s should_create_incident=%s reason=%s",
        platform,
        success_rate_pct,
        _QUERY_SUCCESS_INCIDENT_THRESHOLD_PCT,
        stats["total_count"],
        should_create,
        reason,
    )
    if not should_create:
        return

    await create_pagerduty_incident(
        title="Rolling query success dropped to 25% or below",
        details=(
            f"Rolling 30-minute query success dropped to {success_rate_pct:.2f}% "
            f"(threshold={_QUERY_SUCCESS_INCIDENT_THRESHOLD_PCT:.2f}%). "
            f"platform={platform}, success_count={stats['success_count']}, "
            f"failure_count={stats['failure_count']}, total_count={stats['total_count']}, "
            f"incident_reason={reason}"
        ),
    )
