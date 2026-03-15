"""Passive Anthropic credit health monitoring and incidenting."""
from __future__ import annotations

import logging
from typing import Any

from anthropic import APIStatusError

from services.incident_throttling import clear_incident_failure, evaluate_incident_creation
from services.pagerduty import create_pagerduty_incident

logger = logging.getLogger(__name__)

_ANTHROPIC_CREDITS_CHECK_NAME = "Anthropic Credits"

# Common Anthropic/API billing exhaustion phrases observed in 4xx responses.
_OUT_OF_CREDITS_PATTERNS = (
    "out of credits",
    "credit balance is too low",
    "insufficient credits",
    "exceeded your current quota",
    "billing",
)


def _extract_error_message(exc: Exception) -> str:
    """Extract a normalized error message from Anthropic exceptions."""
    if isinstance(exc, APIStatusError):
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error_payload = body.get("error")
            if isinstance(error_payload, dict):
                msg = error_payload.get("message")
                if isinstance(msg, str):
                    return msg.lower()
        return str(exc).lower()

    return str(exc).lower()


def is_anthropic_out_of_credits_error(exc: Exception) -> bool:
    """Return True when an Anthropic failure indicates account credits are exhausted."""
    message = _extract_error_message(exc)
    return any(pattern in message for pattern in _OUT_OF_CREDITS_PATTERNS)


async def report_anthropic_call_success(*, source: str) -> None:
    """Clear throttled failure state after a successful Anthropic call."""
    logger.info("Anthropic passive credit health passing source=%s", source)
    await clear_incident_failure(_ANTHROPIC_CREDITS_CHECK_NAME)


async def report_anthropic_call_failure(*, exc: Exception, source: str) -> None:
    """Raise a throttled incident if Anthropic reports exhausted account credits."""
    if not is_anthropic_out_of_credits_error(exc):
        logger.debug("Anthropic failure is not credit exhaustion source=%s error=%s", source, exc)
        return

    logger.warning("Anthropic passive credit health failing source=%s error=%s", source, exc)
    should_create, reason = await evaluate_incident_creation(_ANTHROPIC_CREDITS_CHECK_NAME)
    if not should_create:
        logger.info(
            "Anthropic credit incident suppressed by throttle source=%s reason=%s",
            source,
            reason,
        )
        return

    await create_pagerduty_incident(
        title="Anthropic credits exhausted",
        details=(
            "Passive Anthropic credit health check observed an out-of-credits response. "
            f"Source={source}. Reason={reason}. Error={exc}"
        ),
    )
