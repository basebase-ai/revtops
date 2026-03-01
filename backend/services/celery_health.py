"""Celery worker availability checks and startup incidenting."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.pagerduty import create_pagerduty_incident

logger = logging.getLogger(__name__)


async def _inspect_celery_workers(timeout_seconds: float = 5.0) -> dict[str, Any] | None:
    """Return Celery inspect ping response, or None when no workers reply."""
    from workers.celery_app import celery_app

    def _ping() -> dict[str, Any] | None:
        inspector = celery_app.control.inspect(timeout=timeout_seconds)
        return inspector.ping()

    return await asyncio.to_thread(_ping)


async def ensure_celery_workers_available() -> bool:
    """Verify Celery worker availability and raise PagerDuty incident if unavailable."""
    logger.info("Checking Celery worker availability at API startup")

    try:
        ping_response = await _inspect_celery_workers()
    except Exception as exc:
        logger.exception("Celery startup health check failed to execute")
        await create_pagerduty_incident(
            title="Celery startup check failed",
            details=(
                "API startup could not verify Celery worker availability. "
                f"Error: {exc}"
            ),
        )
        return False

    if not ping_response:
        logger.error("No Celery workers responded to startup ping")
        await create_pagerduty_incident(
            title="Celery workers unavailable at startup",
            details=(
                "API startup pinged Celery workers but received no responses. "
                "This usually means worker processes are not running or cannot "
                "connect to the broker."
            ),
        )
        return False

    worker_names = sorted(ping_response.keys())
    logger.info("Celery worker startup check succeeded workers=%s", worker_names)
    return True
