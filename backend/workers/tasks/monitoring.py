"""Infrastructure reachability monitoring and PagerDuty alerting tasks."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from config import get_redis_connection_kwargs, settings
from services.pagerduty import create_pagerduty_incident, get_pagerduty_config
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    """Represents reachability status for a monitored dependency."""

    name: str
    healthy: bool
    details: str


async def _check_http_endpoint(name: str, url: str, timeout_s: float = 10.0) -> CheckResult:
    """Check if an HTTP endpoint is reachable and returns a non-5xx response."""
    logger.info("Checking endpoint %s (%s)", name, url)
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code >= 500:
            return CheckResult(name=name, healthy=False, details=f"HTTP {response.status_code} from {url}")
        return CheckResult(name=name, healthy=True, details=f"HTTP {response.status_code} from {url}")
    except Exception as exc:
        logger.exception("Endpoint check failed for %s (%s)", name, url)
        return CheckResult(name=name, healthy=False, details=f"Request failed for {url}: {exc}")


async def _check_redis(timeout_s: float = 10.0) -> CheckResult:
    """Check if Redis is reachable via PING."""
    import redis.asyncio as aioredis

    redis_url = settings.REDIS_URL
    logger.info("Checking Redis reachability via %s", redis_url)
    redis_client = aioredis.from_url(
        redis_url,
        **get_redis_connection_kwargs(),
    )

    try:
        async with redis_client:
            is_ok = await redis_client.ping()
        if is_ok:
            return CheckResult(name="Redis", healthy=True, details="PING returned true")
        return CheckResult(name="Redis", healthy=False, details="PING returned false")
    except Exception as exc:
        logger.exception("Redis health check failed")
        return CheckResult(name="Redis", healthy=False, details=f"Redis ping failed: {exc}")


async def _create_pagerduty_incident(
    *,
    check_result: CheckResult,
) -> None:
    """Create an incident in PagerDuty v2 REST API."""
    await create_pagerduty_incident(
        title=f"{check_result.name} is down",
        details=(
            "Automated Revtops dependency monitor detected an outage. "
            f"Dependency: {check_result.name}. Details: {check_result.details}"
        ),
    )


async def _run_dependency_checks() -> list[CheckResult]:
    """Run all dependency checks and return results."""
    checks = [
        _check_http_endpoint("Supabase", settings.SUPABASE_URL or "https://supabase.com"),
        _check_http_endpoint("Nango", settings.NANGO_HOST),
        _check_redis(),
        _check_http_endpoint("www.revtops.com", "https://www.revtops.com"),
        _check_http_endpoint("api.revtops.com", "https://api.revtops.com/health"),
    ]

    return [await check for check in checks]


@celery_app.task(bind=True, name="workers.tasks.monitoring.monitor_dependencies")
def monitor_dependencies(self: Any) -> dict[str, Any]:
    """Periodic task: monitor key dependencies and open PagerDuty incidents if down."""
    import asyncio

    logger.info("Task %s: Starting dependency monitoring run", self.request.id)
    pagerduty_config = get_pagerduty_config()
    if pagerduty_config is None:
        return {
            "status": "skipped",
            "reason": "missing_pagerduty_config",
        }

    async def _run() -> dict[str, Any]:
        results = await _run_dependency_checks()
        down = [result for result in results if not result.healthy]

        for result in results:
            level = logging.INFO if result.healthy else logging.WARNING
            logger.log(level, "Dependency check: %s healthy=%s (%s)", result.name, result.healthy, result.details)

            if result.healthy:
                logger.info(
                    "PagerDuty health check succeeded for %s; incident creation skipped",
                    result.name,
                )
            else:
                logger.warning(
                    "PagerDuty health check failed for %s; incident will be created",
                    result.name,
                )

        for result in down:
            await _create_pagerduty_incident(
                check_result=result,
            )

        return {
            "status": "ok",
            "total_checks": len(results),
            "down_count": len(down),
            "down_services": [result.name for result in down],
        }

    return asyncio.run(_run())
