"""Rolling query outcome metrics for messenger-originated turns."""
from __future__ import annotations

import logging
import time

import redis.asyncio as aioredis

from config import get_redis_connection_kwargs, settings

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 15 * 60
_SUCCESS_KEY = "monitoring:query_outcomes:success"
_FAILURE_KEY = "monitoring:query_outcomes:failure"


async def record_query_outcome(*, platform: str, was_success: bool) -> None:
    """Record one query outcome and maintain a rolling 15-minute success pct."""
    timestamp = int(time.time())
    score = float(timestamp)
    bucket_key = _SUCCESS_KEY if was_success else _FAILURE_KEY
    member = f"{timestamp}:{platform}:{'ok' if was_success else 'fail'}:{time.time_ns()}"
    window_start = timestamp - _WINDOW_SECONDS

    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        **get_redis_connection_kwargs(),
    )

    async with redis_client:
        pipe = redis_client.pipeline()
        pipe.zadd(bucket_key, {member: score})
        pipe.expire(bucket_key, _WINDOW_SECONDS * 2)
        pipe.zremrangebyscore(_SUCCESS_KEY, "-inf", window_start)
        pipe.zremrangebyscore(_FAILURE_KEY, "-inf", window_start)
        pipe.zcard(_SUCCESS_KEY)
        pipe.zcard(_FAILURE_KEY)
        _, _, _, _, success_count, failure_count = await pipe.execute()

    success_total = int(success_count or 0)
    failure_total = int(failure_count or 0)
    total = success_total + failure_total
    success_pct = ((success_total / total) * 100.0) if total else 0.0

    logger.info(
        "Rolling query outcomes window_seconds=%s platform=%s was_success=%s "
        "success_count=%s failure_count=%s success_pct=%.2f",
        _WINDOW_SECONDS,
        platform,
        was_success,
        success_total,
        failure_total,
        success_pct,
    )
