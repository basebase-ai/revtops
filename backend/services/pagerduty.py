"""PagerDuty incident helpers shared across API and worker tasks."""
from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


def get_pagerduty_config() -> tuple[str, str, str] | None:
    """Return PagerDuty settings if complete, else log and skip."""
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
    config = get_pagerduty_config()
    if config is None:
        return False

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
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api.pagerduty.com/incidents",
            json=payload,
            headers=headers,
        )
    if response.status_code >= 300:
        logger.error(
            "PagerDuty incident creation failed for %s: HTTP %s - %s",
            title,
            response.status_code,
            response.text,
        )
        return False

    logger.info("PagerDuty incident created for %s with status %s", title, response.status_code)
    return True
