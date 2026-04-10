"""PagerDuty incident helpers shared across API and worker tasks."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from config import settings

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PagerDutyIncidentResult:
    """Result metadata for PagerDuty incident creation attempts."""

    ok: bool
    reason: str
    status_code: int | None = None
    response_body: str | None = None


def _pagerduty_incidents_enabled() -> bool:
    """Return True when PagerDuty incident creation is explicitly enabled."""
    if settings.PAGERDUTY_INCIDENTS_ENABLED:
        return True

    logger.info(
        "PagerDuty incident skipped: incidenting disabled "
        "(parsed_enabled=%s raw_env=%r)",
        settings.PAGERDUTY_INCIDENTS_ENABLED,
        os.getenv("PAGERDUTY_INCIDENTS_ENABLED"),
    )
    return False


def get_pagerduty_config() -> tuple[str, str, str] | None:
    """Return PagerDuty settings if complete, else log and skip."""
    if not _pagerduty_incidents_enabled():
        return None

    from_email = settings.PAGERDUTY_FROM_EMAIL
    api_key = settings.PAGERDUTY_KEY
    service_id = settings.PAGERDUTY_SERVICE_ID

    if not from_email or not api_key or not service_id:
        logger.warning(
            "PagerDuty incident skipped: missing configuration "
            "(PAGERDUTY_FROM_EMAIL=%s, PagerDuty_Key=%s, PAGERDUTY_SERVICE_ID=%s)",
            bool(from_email),
            bool(api_key),
            bool(service_id),
        )
        return None
    return from_email, api_key, service_id


async def create_pagerduty_incident(*, title: str, details: str) -> bool:
    """Create a PagerDuty incident and return True if request was accepted."""
    result = await create_pagerduty_incident_with_details(title=title, details=details)
    return result.ok


async def create_pagerduty_incident_with_details(*, title: str, details: str) -> PagerDutyIncidentResult:
    """Create a PagerDuty incident and return structured status details."""
    config = get_pagerduty_config()
    if config is None:
        return PagerDutyIncidentResult(ok=False, reason="missing_config")

    from_email, api_key, service_id = config
    payload = {
        "incident": {
            "type": "incident",
            "title": title,
            "service": {
                "id": service_id,
                "type": "service_reference",
            },
            "urgency": "high",
            "body": {
                "type": "incident_body",
                "details": details,
            },
        }
    }
    headers = {
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json",
        "Authorization": f"Token token={api_key}",
        "From": from_email,
    }

    logger.warning("Creating PagerDuty incident: %s", title)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://api.pagerduty.com/incidents",
                json=payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        logger.exception("PagerDuty incident creation failed for %s: transport error", title)
        return PagerDutyIncidentResult(ok=False, reason=f"transport_error:{type(exc).__name__}")

    if response.status_code >= 300:
        logger.error(
            "PagerDuty incident creation failed for %s: HTTP %s - %s",
            title,
            response.status_code,
            response.text,
        )
        return PagerDutyIncidentResult(
            ok=False,
            reason="http_error",
            status_code=response.status_code,
            response_body=response.text,
        )

    logger.info("PagerDuty incident created for %s with status %s", title, response.status_code)
    return PagerDutyIncidentResult(ok=True, reason="created", status_code=response.status_code)
