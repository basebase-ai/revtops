"""Celery worker availability checks and startup incidenting."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from services.pagerduty import create_pagerduty_incident_with_details

logger = logging.getLogger(__name__)

DEFAULT_STARTUP_PING_ATTEMPTS = 3
DEFAULT_STARTUP_RETRY_DELAY_SECONDS = 2.0


def _env_override_truthy(name: str) -> bool | None:
    """If env is set to a known on/off value, return that; else None (inherit default)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "force", "on"):
        return True
    if v in ("0", "false", "no", "skip", "off"):
        return False
    return None


def celery_startup_check_enabled() -> bool:
    """Whether the API should ping Celery workers before accepting traffic.

    Disabled in development by default so local uvicorn restarts are not blocked
    by missing Redis/workers (each failed ping can take up to several seconds).

    Override: ``CELERY_STARTUP_CHECK=true`` (or ``force``) to enable in dev;
    ``CELERY_STARTUP_CHECK=false`` (or ``skip``) to disable in any environment.
    """
    override = _env_override_truthy("CELERY_STARTUP_CHECK")
    if override is False:
        return False
    if override is True:
        return True
    env = os.getenv("ENVIRONMENT", "development").strip().lower()
    return env != "development"


def _emit_startup_incident_nonblocking(*, title: str, details: str) -> None:
    """Trigger startup incident creation without blocking API startup."""

    async def _create_and_log() -> None:
        result = await create_pagerduty_incident_with_details(title=title, details=details)
        if result.ok:
            logger.info(
                "Startup incident request accepted title=%s status=%s reason=%s",
                title,
                result.status_code,
                result.reason,
            )
            return

        logger.error(
            "Startup incident request failed title=%s reason=%s status=%s body=%s",
            title,
            result.reason,
            result.status_code,
            result.response_body,
        )

    task = asyncio.create_task(_create_and_log())

    def _on_done(completed_task: asyncio.Task[None]) -> None:
        try:
            completed_task.result()
        except Exception:
            logger.exception("Startup incident task crashed title=%s", title)

    task.add_done_callback(_on_done)


async def _inspect_celery_workers(timeout_seconds: float = 5.0) -> dict[str, Any] | None:
    """Return Celery inspect ping response, or None when no workers reply."""
    from workers.celery_app import celery_app

    def _ping() -> dict[str, Any] | None:
        inspector = celery_app.control.inspect(timeout=timeout_seconds)
        return inspector.ping()

    return await asyncio.to_thread(_ping)


async def ensure_celery_workers_available() -> bool:
    """Verify Celery worker availability and raise PagerDuty incident if unavailable."""
    if not celery_startup_check_enabled():
        logger.info(
            "Skipping Celery worker startup check (development default, or "
            "CELERY_STARTUP_CHECK disabled). Set CELERY_STARTUP_CHECK=true to enable."
        )
        return True

    max_attempts = max(1, int(os.getenv("CELERY_STARTUP_PING_ATTEMPTS", DEFAULT_STARTUP_PING_ATTEMPTS)))
    retry_delay_seconds = max(
        0.0,
        float(os.getenv("CELERY_STARTUP_RETRY_DELAY_SECONDS", DEFAULT_STARTUP_RETRY_DELAY_SECONDS)),
    )
    logger.info(
        "Checking Celery worker availability at API startup attempts=%s retry_delay_seconds=%.2f",
        max_attempts,
        retry_delay_seconds,
    )

    last_exception: Exception | None = None
    saw_no_worker_responses = False
    for attempt in range(1, max_attempts + 1):
        try:
            ping_response = await _inspect_celery_workers()
        except Exception as exc:  # pragma: no cover - tested via behavior
            last_exception = exc
            logger.warning(
                "Celery startup health check attempt %s/%s raised error: %s",
                attempt,
                max_attempts,
                exc,
            )
        else:
            if ping_response:
                worker_names = sorted(ping_response.keys())
                logger.info(
                    "Celery worker startup check succeeded attempt=%s/%s workers=%s",
                    attempt,
                    max_attempts,
                    worker_names,
                )
                return True

            saw_no_worker_responses = True
            logger.warning(
                "Celery startup health check attempt %s/%s received no worker responses",
                attempt,
                max_attempts,
            )

        if attempt < max_attempts and retry_delay_seconds > 0:
            await asyncio.sleep(retry_delay_seconds)

    if last_exception is not None:
        logger.exception("Celery startup health check failed after %s attempts", max_attempts, exc_info=last_exception)
        _emit_startup_incident_nonblocking(
            title="Celery startup check failed",
            details=(
                "API startup could not verify Celery worker availability after "
                f"{max_attempts} attempts. Error: {last_exception}"
            ),
        )
        return False

    if saw_no_worker_responses:
        logger.error("No Celery workers responded to startup ping after %s attempts", max_attempts)
        _emit_startup_incident_nonblocking(
            title="Celery workers unavailable at startup",
            details=(
                "API startup pinged Celery workers but received no responses after "
                f"{max_attempts} attempts. This usually means worker processes are "
                "not running or cannot connect to the broker."
            ),
        )
        return False

    logger.error(
        "Celery startup health check exhausted attempts=%s without clear signal; skipping incident",
        max_attempts,
    )
    return False
