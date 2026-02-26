"""PagerDuty incident helpers."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


async def create_incident(*, title: str, details: str, source: str) -> bool:
    """Create an incident in PagerDuty. Returns True when PagerDuty accepts the request."""
    from_email = settings.PAGERDUTY_FROM_EMAIL
    api_key = settings.PAGERDUTY_KEY
    service_id = settings.PAGERDUTY_SERVICE_ID

    if not from_email or not api_key or not service_id:
        logger.warning(
            "Skipping PagerDuty incident for source=%s due to missing configuration "
            "(PAGERDUTY_FROM_EMAIL=%s, PagerDuty_Key=%s, PAGERDUTY_SERVICE_ID=%s)",
            source,
            bool(from_email),
            bool(api_key),
            bool(service_id),
        )
        return False

    payload: dict[str, Any] = {
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

    logger.warning("Creating PagerDuty incident for source=%s title=%s", source, title)
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api.pagerduty.com/incidents",
            json=payload,
            headers=headers,
        )

    if response.status_code >= 300:
        logger.error(
            "PagerDuty incident creation failed for source=%s: HTTP %s - %s",
            source,
            response.status_code,
            response.text,
        )
        return False

    logger.info(
        "PagerDuty incident created for source=%s with status=%s",
        source,
        response.status_code,
    )
    return True
