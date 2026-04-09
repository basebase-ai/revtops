"""Redis-backed throttling for recurring dependency outage incidents."""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

import redis.asyncio as aioredis

from config import get_redis_connection_kwargs, settings

logger = logging.getLogger(__name__)

_INCIDENT_COOLDOWN_SECONDS = 90 * 60
_INCIDENT_KEY_PREFIX = "monitoring:incident_throttle"
_INCIDENT_KEY_TTL_SECONDS = 7 * 24 * 60 * 60


def _incident_key(check_name: str) -> str:
    """Build Redis key for check-level incident throttling state."""
    return f"{_INCIDENT_KEY_PREFIX}:{quote(check_name, safe='')}"


async def evaluate_incident_creation(check_name: str) -> tuple[bool, str]:
    """Return whether a new incident should be raised for a failing check."""
    now = int(time.time())
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        **get_redis_connection_kwargs(decode_responses=True),
    )
    key = _incident_key(check_name)

    try:
        async with redis_client:
            payload = await redis_client.hgetall(key)
            if not payload:
                await redis_client.hset(
                    key,
                    mapping={
                        "first_failed_at": str(now),
                        "last_incident_at": str(now),
                    },
                )
                await redis_client.expire(key, _INCIDENT_KEY_TTL_SECONDS)
                return True, "new_failure"

            last_incident_raw = payload.get("last_incident_at")
            try:
                last_incident_at = int(last_incident_raw) if last_incident_raw is not None else 0
            except ValueError:
                logger.warning(
                    "Invalid incident throttle timestamp for check=%s payload=%s",
                    check_name,
                    payload,
                )
                last_incident_at = 0

            if now - last_incident_at >= _INCIDENT_COOLDOWN_SECONDS:
                await redis_client.hset(key, mapping={"last_incident_at": str(now)})
                await redis_client.expire(key, _INCIDENT_KEY_TTL_SECONDS)
                return True, "cooldown_elapsed"

            suppress_for = _INCIDENT_COOLDOWN_SECONDS - (now - last_incident_at)
            return False, f"suppressed_for_{max(0, suppress_for)}s"
    except Exception:
        logger.exception(
            "Incident throttling unavailable for check=%s; failing open and allowing incident",
            check_name,
        )
        return True, "throttle_unavailable"


async def clear_incident_failure(check_name: str) -> None:
    """Clear failure-throttle state after a check recovers."""
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        **get_redis_connection_kwargs(),
    )
    key = _incident_key(check_name)

    try:
        async with redis_client:
            deleted = await redis_client.delete(key)
        if deleted:
            logger.info("Cleared incident throttle state for recovered check=%s", check_name)
    except Exception:
        logger.exception("Failed to clear incident throttle state for check=%s", check_name)
